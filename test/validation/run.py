#!/usr/bin/env python3
"""Run independent suites, retaining logs/XML/traces and all failures."""
import argparse,base64,hashlib,json,os,platform,re,subprocess,sys
from datetime import datetime,timezone
from importlib.metadata import version
from pathlib import Path
import xml.etree.ElementTree as ET


def main():
    here=Path(__file__).resolve().parent
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--suite',nargs='+',choices=['all','mac','activation','spi','soc'],default=['all'])
    p.add_argument('--sim',choices=['verilator','icarus'],default='verilator')
    p.add_argument('--test',default='',help='Python regex over full cocotb test name')
    p.add_argument('--trace',choices=['quiet','steps','cycles'],default='quiet')
    p.add_argument('--protocol',action='store_true')
    p.add_argument('--seed',default='260920')
    p.add_argument('--stress',action='store_true')
    p.add_argument('--input-contract',choices=['rtl','edge'],default='rtl')
    p.add_argument('--spi-div',type=int,default=1)
    p.add_argument('--output',type=Path,default=here/'results')
    a=p.parse_args()
    try:re.compile(a.test);int(a.seed,0)
    except (ValueError,re.error) as e:p.error(str(e))
    if a.spi_div<1:p.error('SPI divider must be positive')
    output=a.output.resolve();output.mkdir(parents=True,exist_ok=True)
    suites=['mac','activation','spi','soc'] if 'all' in a.suite else list(dict.fromkeys(a.suite))
    rev=subprocess.run(['git','rev-parse','HEAD'],cwd=here,capture_output=True,text=True).stdout.strip()
    try:
        sim=subprocess.run(['verilator','--version'] if a.sim=='verilator' else ['iverilog','-V'],capture_output=True,text=True,check=True).stdout.splitlines()[0]
    except (OSError,subprocess.CalledProcessError,IndexError) as e:p.error(f'Simulator unavailable: {e}')
    files=list(here.glob('*.py'))+list(here.glob('*.sv'))+[here/'Makefile',here.parent/'requirements.txt']+list((here/'examples').glob('*.json'))+list((here/'../../src').resolve().glob('*'))
    hashes={str(f.resolve().relative_to(here.parent.parent)):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(files) if f.is_file()}
    summary=dict(commit=rev,started_utc=datetime.now(timezone.utc).isoformat(),source_sha256=hashes,
        tools=dict(python=platform.python_version(),cocotb=version('cocotb'),simulator=sim),
        configuration={k:str(v) if isinstance(v,Path) else v for k,v in vars(a).items()},suites={})
    failed=False
    for suite in suites:
        directory=output/suite;directory.mkdir(exist_ok=True)
        xml=directory/'results.xml';xml.unlink(missing_ok=True)
        env=os.environ|{'NPU_TRACE':a.trace,'NPU_PROTOCOL':str(int(a.protocol)),'NPU_SEED':a.seed,
            'NPU_STRESS':str(int(a.stress)),'NPU_INPUT_CONTRACT':a.input_contract,'NPU_SPI_DIV':str(a.spi_div),
            'NPU_OUTPUT':str(directory),'COCOTB_TEST_FILTER':'','COCOTB_TESTCASE':'',
            'NPU_FILTER_B64':base64.b64encode(a.test.encode()).decode()}
        cmd=['make','-f',str(here/'Makefile'),f'SUITE={suite}',f'SIM={a.sim}',f'SPI_DIV={a.spi_div}',
             f'SIM_BUILD={here/"build"/f"{a.sim}-{suite}-spi{a.spi_div}"}',f'COCOTB_RESULTS_FILE={xml}']
        print(f'Running {suite}; log: {directory/"sim.log"}',flush=True)
        with (directory/'sim.log').open('w') as log:
            proc=subprocess.run(cmd,cwd=here,env=env,stdout=log,stderr=subprocess.STDOUT)
        cases=[]
        if xml.exists():
            for n in ET.parse(xml).iter('testcase'):
                status='fail' if n.find('failure') is not None or n.find('error') is not None else 'skip' if n.find('skipped') is not None else 'pass'
                cases.append(dict(name=n.get('name'),status=status))
        counts={s:sum(c['status']==s for c in cases) for s in ('pass','fail','skip')}
        infra=not (counts['pass'] or counts['fail']) or (proc.returncode!=0 and not counts['fail'])
        failed |= bool(infra or counts['fail'])
        summary['suites'][suite]=dict(returncode=proc.returncode,passed=counts['pass'],failed=counts['fail'],skipped=counts['skip'],infrastructure_error=infra,cases=cases)
        (output/'summary.json').write_text(json.dumps(summary,indent=2)+'\n')
        print(f'  {counts}; infrastructure_error={infra}',flush=True)
    return int(failed)

if __name__=='__main__':sys.exit(main())
