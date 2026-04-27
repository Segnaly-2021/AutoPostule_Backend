from dataclasses import dataclass
from uuid import UUID

from auto_apply_app.domain.entities.entity import Entity


@dataclass
class UserFingerprint(Entity):
	user_id: UUID
	user_agent: str
	viewport_width: int
	viewport_height: int
	device_scale_factor: float
	locale: str
	timezone_id: str
	hardware_concurrency: int
	platform: str
	webgl_vendor: str
	webgl_renderer: str

	def to_playwright_context_args(self) -> dict:
		"""
		Returns the subset of fingerprint fields that Playwright accepts
		directly in browser.new_context(). Hardware concurrency and WebGL
		need to be injected via init scripts (see to_init_script).
		"""
		return {
			"user_agent": self.user_agent,
			#"viewport": {"width": self.viewport_width, "height": self.viewport_height},
			"device_scale_factor": self.device_scale_factor,
			"locale": self.locale,
			"timezone_id": self.timezone_id,
		}

	def to_init_script(self) -> str:
		"""
		Returns a JavaScript snippet to inject via context.add_init_script().
		Overrides navigator and WebGL properties that Playwright doesn't
		expose directly through context options.
		"""
		return f"""
		Object.defineProperty(navigator, 'hardwareConcurrency', {{
			get: () => {self.hardware_concurrency}
		}});
		Object.defineProperty(navigator, 'platform', {{
			get: () => '{self.platform}'
		}});

		const getParameter = WebGLRenderingContext.prototype.getParameter;
		WebGLRenderingContext.prototype.getParameter = function(parameter) {{
			if (parameter === 37445) return '{self.webgl_vendor}';
			if (parameter === 37446) return '{self.webgl_renderer}';
			return getParameter.call(this, parameter);
		}};
		"""