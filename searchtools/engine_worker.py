"""Run inside each upstream tool's own Python environment, never the UI process."""
import asyncio
import json
import os
from pathlib import Path
import sys


def main():
    job=json.loads(Path(sys.argv[1]).read_text())
    if job['kind']=='research':
        from dotenv import load_dotenv
        load_dotenv(Path.cwd()/'.env')
        os.environ['DOC_PATH']=job['papers']
        os.environ['REPORT_SOURCE']=job['source']
        from gpt_researcher import GPTResearcher
        async def research():
            agent=GPTResearcher(query=job['prompt'],report_type='research_report',report_source=job['source'])
            await agent.conduct_research()
            answer=await agent.write_report()
            return {'answer':answer,'sources':agent.get_research_sources()}
        result=asyncio.run(research())
    else:
        from paperqa import Settings
        from paperqa.agents import ask,build_index
        cfg=json.loads(Path(job['settings']).read_text())
        cfg.setdefault('agent',{}).setdefault('index',{}).update({
            'name':'project-'+job['project'], 'paper_directory':job['papers'], 'index_directory':job['index']})
        settings=Settings(**cfg)
        if job['kind']=='index':
            build_index(settings=settings)
            result={'answer':'项目文献索引已完成。'}
        else:
            response=ask(job['prompt'],settings=settings)
            result=json.loads(response.model_dump_json())
            result['answer']=response.session.formatted_answer or response.session.answer
            result['successful']=response.session.has_successful_answer
    result['input_manifest']=job['manifest']
    result['question']=job['prompt']
    Path(job['output']).write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str))
    print('SearchTools task finished',flush=True)

if __name__=='__main__':
    main()
