#!/usr/bin/env python3
"""
Estimate costs before running experiments.
Run this FIRST to understand what you're signing up for.
"""

def estimate():
    print("=" * 60)
    print("REPOWISE BENCHMARK COST ESTIMATES")
    print("=" * 60)

    # Sonnet pricing (as of early 2026, verify before running)
    INPUT_PER_MTok = 3.0   # $/M input tokens
    OUTPUT_PER_MTok = 15.0  # $/M output tokens

    print("\n--- SWE-QA (Codebase Understanding) ---")
    print("576 questions × 4 conditions = 2,304 runs")
    print("Each run: ~5K input tokens, ~1K output tokens (single call, not agentic)")
    swe_qa_input = 2304 * 5000 / 1_000_000 * INPUT_PER_MTok
    swe_qa_output = 2304 * 1000 / 1_000_000 * OUTPUT_PER_MTok
    swe_qa_judge = 2304 * 3000 / 1_000_000 * (INPUT_PER_MTok + OUTPUT_PER_MTok * 0.3)
    swe_qa_total = swe_qa_input + swe_qa_output + swe_qa_judge
    print(f"  Agent calls:   ${swe_qa_input + swe_qa_output:.0f}")
    print(f"  LLM-as-judge:  ${swe_qa_judge:.0f}")
    print(f"  TOTAL:         ${swe_qa_total:.0f}")
    print(f"  Est. time:     8-12 hours (parallelizable per condition)")

    print("\n--- SWE-bench Verified (Bug Fixing) ---")
    print("100 tasks × 4 conditions = 400 runs")
    print("Each run: ~50K input tokens, ~10K output tokens (agentic, multi-turn)")
    swe_input = 400 * 50000 / 1_000_000 * INPUT_PER_MTok
    swe_output = 400 * 10000 / 1_000_000 * OUTPUT_PER_MTok
    swe_total = swe_input + swe_output
    print(f"  Agent calls:   ${swe_total:.0f}")
    print(f"  No judge needed (test suite pass/fail)")
    print(f"  TOTAL:         ${swe_total:.0f}")
    print(f"  Est. time:     12-24 hours")

    print("\n--- FEA-Bench Lite (Feature Implementation) ---")
    print("50 tasks × 4 conditions = 200 runs")
    print("Each run: ~60K input tokens, ~12K output tokens (complex multi-file edits)")
    fea_input = 200 * 60000 / 1_000_000 * INPUT_PER_MTok
    fea_output = 200 * 12000 / 1_000_000 * OUTPUT_PER_MTok
    fea_total = fea_input + fea_output
    print(f"  Agent calls:   ${fea_total:.0f}")
    print(f"  TOTAL:         ${fea_total:.0f}")
    print(f"  Est. time:     6-12 hours")

    print("\n--- Repowise Indexing Costs ---")
    print("Doc generation uses Gemini Flash Lite (~$0.075/M input)")
    print("12 repos × ~500 files average × ~2K tokens per file")
    index_cost = 12 * 500 * 2000 / 1_000_000 * 0.075
    print(f"  TOTAL:         ${index_cost:.1f} (negligible)")

    grand_total = swe_qa_total + swe_total + fea_total + index_cost
    print("\n" + "=" * 60)
    print(f"GRAND TOTAL (all benchmarks):  ${grand_total:.0f}")
    print(f"SWE-QA only (start here):      ${swe_qa_total:.0f}")
    print("=" * 60)

    print("\n⚡ RECOMMENDATION:")
    print("1. Run SWE-QA first (~$" + f"{swe_qa_total:.0f}" + ")")
    print("2. Check signal with analysis/quick_check.py")
    print("3. Only run SWE-bench if SWE-QA shows >10% improvement")
    print("4. FEA-Bench is optional stretch — skip for ESEM abstract")

    print("\n💡 COST SAVING TIPS:")
    print("- Use Haiku for LLM-as-judge (cheaper, comparable quality)")
    print("- Run C0 and C2 first (skip C1/C3 initially)")
    print("- SWE-QA: batch by repo to reuse indexes")
    print("- Cache Repowise indexes — they don't change between conditions")


if __name__ == "__main__":
    estimate()
