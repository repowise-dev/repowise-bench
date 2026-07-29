"""Command line entry point: one snapshot, one question set, one result blob.

    python -m answer_eval --index 45ce57f52457

Reuses a cached snapshot and a previously built index when they are already on
disk, so re-running against the same corpus costs nothing. ``--rebuild-index``
forces the build; that one does spend, because it embeds every page.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from answer_eval.index import build_index, read_build_report, write_build_report
from answer_eval.question_set import load_retrieval_questions
from answer_eval.runner import DEFAULT_K, run_question_set, write_blob
from answer_eval.server_session import EmbedderConfig, answer_server, resolve_synthesis_model
from answer_eval.snapshot import fetch_snapshot

logger = logging.getLogger("answer_eval")

DEFAULT_WORK_DIR = Path("results/answer-eval")
DEFAULT_QUESTIONS = Path("answer_eval/questions/retrieval.jsonl")

#: Pinned, not discovered. 768 dims is what the hosted index's vector column
#: holds, so a run here is at least dimensionally comparable to what users get.
DEFAULT_EMBEDDER = EmbedderConfig(name="gemini", model="gemini-embedding-001", dims=768)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="answer_eval", description=__doc__)
    parser.add_argument(
        "--index",
        required=True,
        metavar="SHORT_ID",
        help="hosted snapshot short id - pins the corpus a run is measured against",
    )
    parser.add_argument(
        "--questions",
        type=Path,
        default=DEFAULT_QUESTIONS,
        help=f"JSONL retrieval question set (default: {DEFAULT_QUESTIONS})",
    )
    parser.add_argument("--k", type=int, default=DEFAULT_K, help="the k in recall@k")
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=DEFAULT_WORK_DIR,
        help=f"where snapshots, indexes and blobs live (default: {DEFAULT_WORK_DIR})",
    )
    parser.add_argument("--out", type=Path, default=None, help="result blob path")
    parser.add_argument(
        "--rebuild-index",
        action="store_true",
        help="rebuild the index even if one exists - re-embeds every page",
    )
    parser.add_argument(
        "--embedder", default=DEFAULT_EMBEDDER.name, help="embedder name to pin the run to"
    )
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDER.model)
    parser.add_argument("--embedding-dims", type=int, default=DEFAULT_EMBEDDER.dims)
    parser.add_argument(
        "--checkout",
        type=Path,
        default=None,
        help=(
            "source tree at the snapshot's commit. Without it the index holds pages "
            "alone and every answer is capped at medium confidence, because the "
            "citation-source gate demotes any high answer that cannot cite symbol "
            "bodies. The index is written inside the checkout, as a real repo's is."
        ),
    )
    parser.add_argument(
        "--commit",
        default=None,
        help="commit the snapshot was generated from; the checkout is refused if it differs",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


async def run(args: argparse.Namespace) -> Path:
    embedder = EmbedderConfig(
        name=args.embedder, model=args.embedding_model, dims=args.embedding_dims
    )

    questions = load_retrieval_questions(args.questions)
    logger.info("loaded %d questions from %s", len(questions), args.questions)

    work_dir = Path(args.work_dir)
    pages = fetch_snapshot(args.index, work_dir / "snapshots")
    logger.info("snapshot %s has %d pages", args.index, len(pages))

    # With a checkout, the index goes inside it. The answer tool reads symbol
    # source live from the repo root it is pointed at, so the database and the
    # source it cites have to be the same tree - exactly as in a real repo.
    repo_dir = (
        Path(args.checkout)
        if args.checkout
        else work_dir / "indexes" / f"{args.index}-{embedder.name}"
    )
    if args.rebuild_index and (repo_dir / ".repowise").exists():
        import shutil

        # Only the index, never the checkout around it.
        shutil.rmtree(repo_dir / ".repowise")

    if (repo_dir / ".repowise" / "wiki.db").is_file():
        # Reused, not rebuilt. Safe only because the build report is on disk
        # next to it - without that the run would report numbers with no
        # record of the embedder or recipe that produced them, and
        # read_build_report refuses rather than guessing.
        index_report = read_build_report(repo_dir)
        logger.info(
            "reusing index at %s (%d pages, embedder=%s, recipe=%s). "
            "Pass --rebuild-index to embed again.",
            repo_dir,
            index_report.pages_written,
            index_report.embedder,
            index_report.embed_recipe,
        )
        if index_report.embedder != embedder.name:
            raise SystemExit(
                f"index at {repo_dir} was built with embedder "
                f"{index_report.embedder!r} but this run is pinned to "
                f"{embedder.name!r}. Querying it would compare vectors from two "
                "different models. Pass --rebuild-index."
            )
    else:
        logger.info("building index at %s - this embeds every page", repo_dir)
        index_report = await build_index(
            pages,
            repo_dir=repo_dir,
            embedder_name=embedder.name,
            checkout=args.checkout,
            expected_commit=args.commit,
        )
        write_build_report(index_report, repo_dir)
        logger.info(
            "index built: %d pages, %d vectors, recipe=%s",
            index_report.pages_written,
            index_report.vectors_written,
            index_report.embed_recipe,
        )

    synthesis = resolve_synthesis_model(repo_dir)
    logger.info("synthesising with %s / %s", synthesis.provider, synthesis.model)

    async with answer_server(repo_dir, embedder) as answer_tool:
        report = await run_question_set(
            answer_tool,
            questions,
            snapshot_short_id=args.index,
            index=index_report,
            embedder=embedder,
            synthesis=synthesis,
            k=args.k,
        )

    out = args.out or work_dir / "blobs" / f"{args.index}-{embedder.name}.json"
    write_blob(report, out)

    print(
        f"recall@{report.k}={report.scores.recall_at_k:.3f} "
        f"(by file {report.recall_at_k_by_file:.3f}) "
        f"mrr={report.scores.mrr:.3f} "
        f"abstain={report.abstention_rate:.1%} "
        f"confidence={report.confidence_counts} "
        f"named-not-cited={report.n_answers_naming_an_uncited_expected_path} "
        f"n={report.scores.n_questions}"
    )
    return out


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    sys.exit(main())
