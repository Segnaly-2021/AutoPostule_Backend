import os
from langchain_google_genai import ChatGoogleGenerativeAI

from auto_apply_app.infrastructures.agent.master.master_agent import MasterAgent
from auto_apply_app.infrastructures.agent.workers.apec.apec_worker import ApecWorker
from auto_apply_app.infrastructures.agent.workers.hellowork.hw_worker_v1 import HelloWorkWorker
from auto_apply_app.infrastructures.agent.workers.wttj.wttj_worker import WelcomeToTheJungleWorker

from auto_apply_app.application.use_cases.agent_use_cases import ProcessAgentResultsUseCase, SaveJobApplicationsUseCase


GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")


model = ChatGoogleGenerativeAI(
  api_key=GEMINI_API_KEY,
  model="gemini-3-pro-preview",
  temperature=0.8,
)


def create_agent(
                 results_processor: ProcessAgentResultsUseCase,
                 results_saver: SaveJobApplicationsUseCase
):
    apec_worker = ApecWorker(
      llm=model, 
      results_processor=results_processor,
      results_saver=results_saver)
    
    hw_worker = HelloWorkWorker(
      llm=model, 
      results_processor=results_processor,
      results_saver=results_saver
    )
    wttj_worker = WelcomeToTheJungleWorker(
      llm=model, 
      results_processor=results_processor,
      results_saver=results_saver)
    
    return MasterAgent(wttj_worker=wttj_worker, hellowork_worker=hw_worker, apec_worker=apec_worker)