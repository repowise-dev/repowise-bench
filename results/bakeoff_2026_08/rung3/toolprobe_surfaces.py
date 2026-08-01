import asyncio, os, sys
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
ROOT=r"C:\Users\ragha\Desktop\repowise"
REPO=ROOT+r"\repowise-bench\repos\pallets\flask"
EXE=ROOT+r"\.venv\Scripts\repowise.exe"
env=os.environ.copy(); env["PYTHONIOENCODING"]="utf-8"; env["PYTHONUTF8"]="1"; env["DO_NOT_TRACK"]="1"
async def probe(label, extra):
    sp=StdioServerParameters(command=EXE, args=["mcp",REPO,"--transport","stdio"]+extra, env=env)
    try:
        async with stdio_client(sp) as (r,w):
            async with ClientSession(r,w) as s:
                await s.initialize()
                t=await s.list_tools()
                print(f"{label}: {len(t.tools)} tools -> {sorted(x.name for x in t.tools)}")
    except Exception as e:
        print(f"{label}: FAILED {type(e).__name__}: {e}")
async def m():
    await probe("default        ", [])
    await probe("--tools OLD-BUG", ["--profile","core"])
    await probe("--tools 4-list ", ["--tools","get_answer,get_symbol,search_codebase,get_context"])
    await probe("--tools lean   ", ["--tools","lean"])
asyncio.run(m())
