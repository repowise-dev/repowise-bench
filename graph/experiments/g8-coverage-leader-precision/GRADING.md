# How a row is graded

The rubric every row on this page was read against. It is G1's, restated for
this arm's fields so a grader never has to guess.

## The question

The tool claims: *the call at `file:line` binds to the declaration
`resolved_to`, which lives at `target_file:target_line`.* Is that claim true?

## The procedure, per row

1. **Open the call site.** Read `<repo>/<file>` at `line`. Record the source
   text of that line verbatim into `source_line`. If the call spans lines, take
   the line the field names and read enough around it to see the whole call.
2. **Establish what the receiver is.** Read the enclosing function or method,
   and the file's imports / `use` / `require` / `using` block. For a method
   call, the question is what type the receiver has; for a bare call, which
   declaration is in scope at that point.
3. **Open the target.** Read `<repo>/<target_file>` at `target_line` and check
   it is a declaration of the thing named in `resolved_to`.
4. **Compare.** Would a compiler, or a careful reader of this language,
   dispatch that call site to that declaration?

## Verdicts

* **`correct`**: the target is the declaration the call actually reaches.
* **`wrong`**: it is not. This includes: a same-named declaration in an
  unrelated module or class; the right method name on the wrong type; a target
  in a test file for a call in production code (or the reverse) where no import
  path connects them; a target that is not a declaration of that name at all.
* **`ambiguous`**: genuinely undecidable from source: a dynamic dispatch with
  several live implementations and nothing at the site to choose between them,
  or an interface method where the concrete type cannot be established.
  **`ambiguous` is never counted as correct.** Use it honestly, not as a way to
  avoid reading.

Overload-level errors, right declaring type and wrong overload, are graded
`wrong`, and additionally flagged `error_class: "overload"` so the alternative
grading can be recovered without regrading. This is what G1 does.

## The rules that make the verdict worth something

* **Grade from source, never from a name.** A row where `resolved_to` ends in
  the same identifier as the call is not thereby correct. Two same-named
  declarations in one repository is the single commonest failure this
  experiment exists to catch, and it is invisible to anyone matching names.
* **Interface and virtual dispatch resolve to a declaration that the call can
  actually reach.** Binding to an implementation the receiver's static type
  cannot be is `wrong`, not `ambiguous`; binding to the interface declaration
  when several implementations exist is `correct`.
* **Self / `this` calls are correct only if the class or one of its bases
  declares the method.** Check the base, do not assume it.
* **A target in a different repository subtree that happens to share a name is
  `wrong`**, even where the name is idiomatic (`get`, `run`, `execute`, `new`).
* **Vendored and parallel trees are traps, not findings.** gitleaks vendors its
  own `regexp` wrapper, caffeine carries a `guava/` compatibility subtree, and
  zod has parallel v3 / v4 / mini trees. A target in one of those can be right.
  Check the import before calling it wrong.
* **The recorded line need not carry the callee token.** Step 1's "read enough
  around it" is the rule, and it decides the multi-line chain: where a site
  records the first line of `A::new()
    .b()
    .c()` and the graded
  callee is `.c`, grade the call, not the line number. Where the site was
  stamped is a resolver detail; the experiment asks whether the target is
  right. Settled 2026-08-23 after three rust rows turned on it. Tightening the
  rule would flip those three and would retroactively narrow an allowance that
  around fifty already-published multi-line rows across seven other languages
  were graded under.
* **A constructor is a call, graded like any other.** `new Foo(..)`,
  `Foo::new(..)` and a rust tuple-struct or tuple-variant `Enum("x")` are all
  call sites: `correct` where the target is the declaration reached, `wrong`
  where it is not - which is why `Saturating(..)` binding a trait impl rather
  than the type's declaration is `wrong`. Settled 2026-08-23. Ruling
  constructors out of the population instead would flip 10 csharp rows and 8
  kotlin rows, collapsing two of the cells the page reports as separating, and
  it was never the convention any of the 540 published rows were graded under.

* **Do not consult the tool's own `confidence` or `strategy` when deciding.**
  Those are what the result is analysed against; letting them steer a verdict
  makes the analysis circular. Read them after, or not at all.
* **Write a reason for every row**, in your own words, naming the evidence: the
  import that connects (or fails to connect) the two files, the receiver's
  declared type, the base class that declares the method. A reason that restates
  the verdict is not a reason.

## Row shape to return

Each graded row keeps the fields it was drawn with and gains three:

```json
{
  "repo": "Ocelot",
  "file": "src/Ocelot/...cs",
  "line": 118,
  "source_line": "the verbatim text at that line",
  "target": "resolved_to, as drawn",
  "origin": "the stratum, as drawn",
  "verdict": "correct | wrong | ambiguous",
  "reason": "why, naming the evidence",
  "error_class": "overload"
}
```

`error_class` is present only where it applies.
