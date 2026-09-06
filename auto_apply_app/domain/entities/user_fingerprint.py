import json
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Optional
from uuid import UUID

from auto_apply_app.domain.entities.entity import Entity


# Chrome ships a new major roughly every 4 weeks. A persona's stored major is the
# one it was born with; derive_session_variant walks it forward so a long-lived
# device looks like it takes updates instead of freezing on one version forever.
UA_TEMPLATE_WINDOWS = (
	"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
	"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
)
UA_TEMPLATE_MAC = (
	"Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
	"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
)


def chrome_major_from_version(version: str) -> Optional[int]:
	"""Major out of a Playwright browser.version string ('149.0.7827.53' -> 149).

	The persona's stored major is a guess made when the device was born; the
	browser that actually launches is the ground truth. A UA claiming 140 while
	the engine answers feature checks like 149 is a self-inconsistency a board
	can read directly, and it got worse the moment APEC started driving a real
	Chrome install instead of the bundled Chromium. Workers align the variant to
	this before building the context. Returns None if the string is unparseable,
	so callers keep the stored major rather than crashing a run over cosmetics.
	"""
	try:
		return int(str(version).split(".", 1)[0])
	except (ValueError, AttributeError, IndexError):
		return None


def build_user_agent(platform: str, chrome_major: int) -> str:
	"""The UA string is derived, never stored as a literal, so bumping a persona's
	Chrome major is a one-field change instead of a re-roll of the whole device."""
	template = UA_TEMPLATE_MAC if platform == "MacIntel" else UA_TEMPLATE_WINDOWS
	return template.format(major=chrome_major)


@dataclass
class UserFingerprint(Entity):
	"""One browser identity.

	A *persona* row is a device: platform, GPU, cores, screen — the things a real
	machine never changes. A *session variant* is an unpersisted copy of a persona
	with the volatile surface re-rolled (window size, canvas/audio noise, and
	sometimes the Chrome major). Only personas are stored; variants live for the
	length of one run. See ResolveRunFingerprintUseCase.
	"""

	user_id: UUID
	viewport_width: int
	viewport_height: int
	screen_width: int
	screen_height: int
	device_scale_factor: float
	locale: str
	timezone_id: str
	hardware_concurrency: int
	device_memory: int
	platform: str
	chrome_major: int
	webgl_vendor: str
	webgl_renderer: str
	canvas_seed: str
	audio_seed: str

	# Pool bookkeeping. `slot` is the persona's index within the user's pool and is
	# what makes the row addressable for an upsert; `board` pins a persona to one
	# job board so that board always sees the same machine.
	slot: int = 0
	board: Optional[str] = None
	session_count: int = 0
	created_at: Optional[datetime] = None
	last_used_at: Optional[datetime] = None
	retired_at: Optional[datetime] = None

	@property
	def user_agent(self) -> str:
		return build_user_agent(self.platform, self.chrome_major)

	@property
	def is_retired(self) -> bool:
		return self.retired_at is not None

	def with_variant(self, **changes) -> "UserFingerprint":
		"""Copy carrying the persona's identity (including `id`, which the session
		store and proxy key off) with volatile fields replaced."""
		variant = replace(self, **changes)
		variant.id = self.id
		return variant

	def to_playwright_context_args(self) -> dict:
		"""The subset Playwright accepts directly in browser.new_context().

		Single source for BOTH launch tracks. They used to disagree — the auth
		track set has_touch/is_mobile and re-set device_scale_factor while the
		scrape track did not, so one run presented two different contexts.
		"""
		return {
			"user_agent": self.user_agent,
			"viewport": {"width": self.viewport_width, "height": self.viewport_height},
			"screen": {"width": self.screen_width, "height": self.screen_height},
			"device_scale_factor": self.device_scale_factor,
			"locale": self.locale,
			"timezone_id": self.timezone_id,
			"has_touch": False,
			"is_mobile": False,
		}

	def to_init_script(self) -> str:
		"""JS injected via context.add_init_script() for everything Playwright
		won't set on a context.

		Every interpolated value goes through json.dumps: the values are ours today,
		but building JS by raw f-string is an injection surface that costs nothing
		to close.

		The canvas/audio noise is the part that actually matters for rotation. Two
		runs on the same persona keep an identical GPU string and core count while
		producing different canvas and audio hashes — which is what two visits from
		one real machine look like.
		"""
		vendor = json.dumps(self.webgl_vendor)
		renderer = json.dumps(self.webgl_renderer)
		platform = json.dumps(self.platform)
		ua = json.dumps(self.user_agent)
		brand_major = json.dumps(str(self.chrome_major))
		ua_platform = json.dumps("macOS" if self.platform == "MacIntel" else "Windows")
		canvas_seed = json.dumps(self.canvas_seed)
		audio_seed = json.dumps(self.audio_seed)

		return f"""
(() => {{
  const defineGetter = (obj, prop, value) => {{
    try {{
      Object.defineProperty(obj, prop, {{ get: () => value, configurable: true }});
    }} catch (e) {{ /* already locked down — leave it alone */ }}
  }};

  defineGetter(navigator, 'hardwareConcurrency', {self.hardware_concurrency});
  defineGetter(navigator, 'deviceMemory', {self.device_memory});
  defineGetter(navigator, 'platform', {platform});
  defineGetter(navigator, 'userAgent', {ua});
  defineGetter(navigator, 'appVersion', {ua}.replace('Mozilla/', ''));

  // userAgentData must agree with navigator.platform. A Win32 platform paired
  // with a "macOS" brand list is a one-line detection.
  try {{
    if (navigator.userAgentData) {{
      defineGetter(navigator.userAgentData, 'platform', {ua_platform});
      defineGetter(navigator.userAgentData, 'brands', [
        {{ brand: 'Chromium', version: {brand_major} }},
        {{ brand: 'Google Chrome', version: {brand_major} }},
        {{ brand: 'Not?A_Brand', version: '24' }},
      ]);
    }}
  }} catch (e) {{}}

  defineGetter(screen, 'width', {self.screen_width});
  defineGetter(screen, 'height', {self.screen_height});
  defineGetter(screen, 'availWidth', {self.screen_width});
  // Leave room for the OS taskbar/menubar, as a real screen does.
  defineGetter(screen, 'availHeight', {self.screen_height - 40});
  defineGetter(window, 'devicePixelRatio', {self.device_scale_factor});

  // --- WebGL -------------------------------------------------------------
  // UNMASKED_VENDOR_WEBGL = 37445, UNMASKED_RENDERER_WEBGL = 37446.
  // Both WebGL1 and WebGL2 prototypes: patching only WebGL1 leaves the two
  // contexts disagreeing about the GPU, which is itself detectable.
  const patchWebGL = (proto) => {{
    if (!proto) return;
    const original = proto.getParameter;
    proto.getParameter = function (parameter) {{
      if (parameter === 37445) return {vendor};
      if (parameter === 37446) return {renderer};
      return original.call(this, parameter);
    }};
  }};
  patchWebGL(window.WebGLRenderingContext && WebGLRenderingContext.prototype);
  patchWebGL(window.WebGL2RenderingContext && WebGL2RenderingContext.prototype);

  // --- Deterministic per-session noise ------------------------------------
  // mulberry32 over a string seed: stable within a session (so repeated reads
  // of the same canvas agree, as they would on real hardware) and different
  // across sessions (so the hash moves).
  const makeRng = (seedStr) => {{
    let h = 1779033703 ^ seedStr.length;
    for (let i = 0; i < seedStr.length; i++) {{
      h = Math.imul(h ^ seedStr.charCodeAt(i), 3432918353);
      h = (h << 13) | (h >>> 19);
    }}
    let a = h >>> 0;
    return () => {{
      a |= 0; a = (a + 0x6D2B79F5) | 0;
      let t = Math.imul(a ^ (a >>> 15), 1 | a);
      t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    }};
  }};

  // Canvas: shift a sparse scatter of subpixels by +/-1. Invisible to a human,
  // moves the hash, and survives re-reads because the rng restarts from the seed.
  const canvasSeed = {canvas_seed};
  const perturb = (imageData) => {{
    const rng = makeRng(canvasSeed);
    const d = imageData.data;
    for (let i = 0; i < d.length; i += 4) {{
      if (rng() < 0.02) {{
        const delta = rng() < 0.5 ? -1 : 1;
        d[i] = Math.max(0, Math.min(255, d[i] + delta));
        d[i + 1] = Math.max(0, Math.min(255, d[i + 1] + delta));
        d[i + 2] = Math.max(0, Math.min(255, d[i + 2] + delta));
      }}
    }}
    return imageData;
  }};

  try {{
    const origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function (...args) {{
      return perturb(origGetImageData.apply(this, args));
    }};

    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function (...args) {{
      try {{
        const ctx = this.getContext('2d');
        if (ctx && this.width > 0 && this.height > 0) {{
          const data = origGetImageData.call(ctx, 0, 0, this.width, this.height);
          ctx.putImageData(perturb(data), 0, 0);
        }}
      }} catch (e) {{ /* tainted or webgl canvas — fall through untouched */ }}
      return origToDataURL.apply(this, args);
    }};
  }} catch (e) {{}}

  // Audio: nudge the frequency bins by a fraction of a dB.
  try {{
    const audioSeed = {audio_seed};
    const origGetFloatFrequencyData = AnalyserNode.prototype.getFloatFrequencyData;
    AnalyserNode.prototype.getFloatFrequencyData = function (array) {{
      origGetFloatFrequencyData.call(this, array);
      const rng = makeRng(audioSeed);
      for (let i = 0; i < array.length; i++) {{
        array[i] += (rng() - 0.5) * 0.1;
      }}
    }};
  }} catch (e) {{}}
}})();
"""
