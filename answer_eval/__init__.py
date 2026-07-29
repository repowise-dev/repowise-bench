"""Evaluation harness for repowise's answer tool.

Two question sets drive it: a retrieval set scored without a model
(recall@k, MRR) and a gold set scored by a judge.

This package currently holds the parts that need neither an index nor a
network -- loading a question set, and the scoring maths. The runner that boots
a server against a named snapshot lands separately.
"""
