import asyncio, os
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=r"C:\Users\ragha\Desktop\repowise"
REPO=ROOT+r"\repowise-bench\repos\pallets\flask"
EXE=ROOT+r"\.venv\Scripts\repowise.exe"
env=os.environ.copy(); env["PYTHONIOENCODING"]="utf-8"; env["PYTHONUTF8"]="1"; env["DO_NOT_TRACK"]="1"
FULL="get_answer,get_symbol,search_codebase,get_context,get_risk,get_why,get_dependency_path,get_overview"
LEAN="get_answer,get_symbol,search_codebase,get_context"
async def probe(label, tools):
    sp=StdioServerParameters(command=EXE, args=["mcp",REPO,"--transport","stdio","--tools",tools], env=env)
    async with stdio_client(sp) as (r,w):
        async with ClientSession(r,w) as s:
            await s.initialize(); t=await s.list_tools()
            got=sorted(x.name for x in t.tools); want=sorted(tools.split(","))
            print(f"{label}: {len(got)} served")
            print(f"   served : {got}")
            if got!=want:
                print(f"   MISMATCH missing={sorted(set(want)-set(got))} extra={sorted(set(got)-set(want))}")
            else: print("   exact match with requested allowlist")
async def m():
    await probe("SERVED_TOOLS_FULL", FULL)
    await probe("SERVED_TOOLS_LEAN", LEAN)
asyncio.run(m())
