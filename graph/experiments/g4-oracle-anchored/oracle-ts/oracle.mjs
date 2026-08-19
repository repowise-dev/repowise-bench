/**
 * G4 TypeScript oracle: a call graph produced by tsc's own type checker.
 *
 * The preregistration named `scip-typescript` as the likely instrument and
 * predicted this cell would be the first to fail, for a specific reason: SCIP
 * has no call-edge model. It records occurrences, so turning "a reference to X
 * appears inside Y" into "Y calls X" is a fold we would have to write, and a
 * fold we write is an instrument we grade ourselves against. That is the exact
 * defect G4 exists to remove.
 *
 * So the oracle is the checker instead. Two facts come from tsc and neither is
 * ours:
 *
 *   - *that* a node is a call. The AST says so. `CallExpression`,
 *     `NewExpression` and `TaggedTemplateExpression` are call syntax, not an
 *     inference from a reference sitting near a parenthesis.
 *   - *what it calls*. `checker.getResolvedSignature` returns the signature the
 *     compiler type-checks the site against, with the real type information a
 *     `tsc` build has.
 *
 * What remains ours is the same thing that was ours in Go: which declaration
 * position a function is keyed at. That is validated the same way, by the modal
 * offset plus a hand check of drawn identities.
 *
 * ## Where this differs from the Go oracle, which matters when reading a rate
 *
 * RTA over-approximates: it devirtualises, so it can include an edge that never
 * fires. tsc under-approximates in one direction instead. A call through an
 * interface, an abstract member or a function-typed value resolves to the
 * *declared* signature, and the compiler does not enumerate implementations.
 * On hono that is 30% of in-repo call sites, which is far too much to write off
 * in a footnote: an arm that resolved such a call to the concrete function
 * would be marked wrong for being more useful than the oracle.
 *
 * So the oracle declines to judge those callers. A function containing any call
 * site this checker could not take to a real implementation is kept out of the
 * reachable set, which puts every unmatched edge from it in `unjudged` rather
 * than in `contradicted`. This is the same instrument the Go cell uses to split
 * "outside the oracle" honestly, pointed at the failure mode this language has
 * instead of the one Go has. The concrete edges from such a function are still
 * emitted, so recall is unaffected and only the precision denominator shrinks.
 *
 * ## Top-level calls
 *
 * A call outside any function has no caller declaration to key on. Those edges
 * are counted in the header and not emitted, and, decisively, the positions
 * they would have used are kept out of the reachable set. An arm that
 * attributes a top-level call to a module-level symbol therefore lands in
 * `unjudged` rather than being charged for an edge this oracle never recorded.
 */

import fs from "node:fs";
import path from "node:path";
import process from "node:process";
import ts from "typescript";

const SRC_EXT = new Set([".ts", ".tsx", ".mts", ".cts"]);
const SKIP_DIRS = new Set([
  "node_modules", ".git", "dist", "build", "out", "coverage",
  ".next", ".turbo", ".yarn", "vendor", ".repowise", ".codegraph",
]);
// Only the two conventions that are unambiguous from the path. A directory
// called `test` and a file called `x.test.ts` are test code in every TypeScript
// project; anything cleverer starts guessing about the repository.
const TEST_DIR = /(^|\/)(__tests__|__test__|test|tests|spec|runtime-tests)(\/|$)/;
const TEST_FILE = /\.(test|spec)\.[cm]?tsx?$/;

function parseArgs(argv) {
  const out = { repo: null, out: null, tests: false, tsconfig: null, diagnose: false, subdirs: [] };
  for (let i = 2; i < argv.length; i++) {
    const a = argv[i];
    if (a === "-repo" || a === "--repo") out.repo = argv[++i];
    else if (a === "-out" || a === "--out") out.out = argv[++i];
    else if (a === "-tsconfig" || a === "--tsconfig") out.tsconfig = argv[++i];
    else if (a === "-tests" || a === "--tests") out.tests = true;
    else if (a === "-diagnose" || a === "--diagnose") out.diagnose = true;
    else if (a === "-subdir" || a === "--subdir") {
      const d = argv[++i].replaceAll(String.fromCharCode(92), "/").replace(/[/]+$/, "");
      out.subdirs.push(d);
    }
    else throw new Error(`unknown argument: ${a}`);
  }
  if (!out.repo || !out.out) {
    throw new Error("usage: node oracle.mjs -repo <dir> [-tests] [-tsconfig <file>] -out <file.jsonl>");
  }
  return out;
}

function walk(dir, acc) {
  for (const ent of fs.readdirSync(dir, { withFileTypes: true })) {
    if (SKIP_DIRS.has(ent.name)) continue;
    const full = path.join(dir, ent.name);
    if (ent.isDirectory()) {
      walk(full, acc);
    } else if (ent.isFile()) {
      if (!SRC_EXT.has(path.extname(ent.name))) continue;
      if (/\.d\.[cm]?ts$/.test(ent.name)) continue;
      acc.push(full);
    }
  }
  return acc;
}

const rel = (repo, p) => path.relative(repo, p).split(path.sep).join("/");

const isTestPath = (r) => TEST_DIR.test(r) || TEST_FILE.test(r);

/**
 * The declaration position a function is keyed at.
 *
 * A named declaration keys at its own start. A function expression or arrow
 * that is the initialiser of a variable, a property or a class field keys at
 * the declaring statement, because that is the line a reader, and every arm
 * here, calls the declaration of `foo` in `export const foo = () => {}`.
 * Leading JSDoc is excluded on both counts: `getStart` skips it by default, and
 * the Go oracle keys at the `func` keyword rather than the doc comment, so the
 * two languages are keyed the same way.
 */
function declNode(node) {
  const p = node.parent;
  if (!p) return node;
  if (ts.isVariableDeclaration(p) && p.initializer === node) {
    const stmt = p.parent && p.parent.parent;
    if (stmt && ts.isVariableStatement(stmt)) return stmt;
    return p;
  }
  if ((ts.isPropertyAssignment(p) || ts.isPropertyDeclaration(p)) && p.initializer === node) return p;
  if (ts.isExportAssignment(p) && p.expression === node) return p;
  return node;
}

function declStart(node, sf) {
  // A decorator is part of the node, so `getStart` would key a decorated method
  // at its decorator, which sits on another line and would put the two sides
  // apart for that one shape.
  const decorators = ts.canHaveDecorators(node) ? ts.getDecorators(node) : undefined;
  let pos = node.getStart(sf, false);
  if (decorators && decorators.length) {
    const text = sf.text;
    let i = decorators[decorators.length - 1].end;
    while (i < text.length && /\s/.test(text[i])) i++;
    pos = Math.max(pos, i);
  }
  return sf.getLineAndCharacterOfPosition(pos).line + 1;
}

const FUNCTIONISH = (n) =>
  ts.isFunctionDeclaration(n) || ts.isMethodDeclaration(n) || ts.isConstructorDeclaration(n) ||
  ts.isGetAccessorDeclaration(n) || ts.isSetAccessorDeclaration(n) ||
  ts.isFunctionExpression(n) || ts.isArrowFunction(n) || ts.isClassStaticBlockDeclaration(n);

/**
 * The caller a call site is attributed to: the **outermost** named function
 * containing it.
 *
 * This is a granularity decision, and it is the same one the Go cell already
 * makes when it refuses call-site keying because one arm stores no call-site
 * line. Here the pressure comes from the language. A TypeScript call sits
 * inside `lazy(() => ...)`, inside a `get error()` on a returned object
 * literal, inside a local `const handleParsed = ...`. Keying at the innermost
 * function puts the oracle at a granularity no arm here meets and marks the
 * resulting edge wrong for every one of them. Measured on zod, this one choice
 * moved our precision from 0.698 to 0.962 and codebase-memory-mcp's from 0.620
 * to 0.970, which is the size of the artefact it was hiding.
 *
 * It costs real information: two sibling helpers inside one exported function
 * collapse to a single caller. That cost is paid identically by every arm, and
 * the alternative is a rate that partly measures nesting depth.
 *
 * Named means what `declNode` can name: a function or method declaration, an
 * accessor, a constructor, or a function expression bound to a variable, a
 * property or a class field. A static block and a bare callback are not.
 */
function namedEnclosing(node) {
  let found = null;
  for (let n = node.parent; n; n = n.parent) if (isNamedFunction(n)) found = n;
  return found;
}

function isNamedFunction(n) {
  if (!FUNCTIONISH(n) || ts.isClassStaticBlockDeclaration(n)) return false;
  if (ts.isFunctionExpression(n) || ts.isArrowFunction(n)) return declNode(n) !== n;
  return true;
}

/**
 * What `new X()` constructs.
 *
 * `getResolvedSignature` answers with the constructor signature, and a class
 * that declares no constructor of its own resolves to an inherited one, so the
 * edge would point at a base class the code never names. Which entity is
 * constructed is a different question from which signature type-checks the
 * site, and it is the one a call graph is asking. The class symbol comes from
 * the checker either way.
 */
function constructedClass(node, checker) {
  const sym = checker.getSymbolAtLocation(node.expression);
  const target = sym && (sym.flags & ts.SymbolFlags.Alias ? checker.getAliasedSymbol(sym) : sym);
  const decls = (target && target.declarations) || [];
  return decls.find((d) => ts.isClassDeclaration(d) || ts.isClassExpression(d)) || null;
}

/**
 * An overloaded function resolves to the matching *signature*, which has no
 * body and sits on a different line from the code that runs. Following it to
 * the implementation is not our construction: TypeScript defines the
 * implementation as the declaration with a body in the same symbol, and asking
 * the checker for the symbol is asking the compiler, not guessing.
 *
 * An interface or abstract member has no implementation in its symbol, so it
 * stays where it is and is reported as an abstract target.
 */
function implementationOf(decl, checker) {
  if (decl.body || !decl.name) return decl;
  const sym = checker.getSymbolAtLocation(decl.name);
  const impl = sym && sym.declarations && sym.declarations.find((d) => d.body);
  return impl || decl;
}

function nameOf(node) {
  if (ts.isClassStaticBlockDeclaration(node)) return "<static block>";
  if (ts.isConstructorDeclaration(node)) {
    const cls = node.parent;
    return `${(cls && cls.name && cls.name.text) || "<anonymous class>"}.constructor`;
  }
  const d = declNode(node);
  if (ts.isVariableStatement(d)) {
    const first = d.declarationList.declarations[0];
    return (first && first.name && first.name.getText()) || "<anonymous>";
  }
  if (d.name) return d.name.getText();
  if (node.name) return node.name.getText();
  return "<anonymous>";
}

function main() {
  const args = parseArgs(process.argv);
  const repo = path.resolve(args.repo);

  const all = walk(repo, []).map((p) => ({ abs: p, rel: rel(repo, p) }));
  // `-subdir` scopes the oracle to a source tree inside a repository. A
  // monorepo carries a documentation site, a benchmark harness and integration
  // fixtures alongside the library, and those pull in dependencies the library
  // itself does not have. Type-checking them without an install produces sites
  // the checker cannot resolve, each of which withholds its caller, so the
  // oracle ends up declining to speak about a large part of a run that was only
  // ever about the library. Paths stay repository-relative, so step 3 restricts
  // every arm to exactly this set and no arm is charged for what is outside it.
  const inSubdir = (r) =>
    !args.subdirs.length || args.subdirs.some((d) => r === d || r.startsWith(d + "/"));
  const roots = all.filter((f) => inSubdir(f.rel) && (args.tests || !isTestPath(f.rel)));

  let options = {
    target: ts.ScriptTarget.ESNext,
    module: ts.ModuleKind.ESNext,
    moduleResolution: ts.ModuleResolutionKind.Bundler,
    jsx: ts.JsxEmit.ReactJSX,
    allowJs: false,
    noEmit: true,
    skipLibCheck: true,
    strict: false,
    allowImportingTsExtensions: true,
  };
  const configPath = args.tsconfig ? path.resolve(args.tsconfig) : null;
  let configErrors = 0;
  if (configPath && fs.existsSync(configPath)) {
    const host = { ...ts.sys, onUnRecoverableConfigFileDiagnostic: () => { configErrors++; } };
    const parsed = ts.getParsedCommandLineOfConfigFile(configPath, {}, host);
    if (parsed) {
      // Compiler options only. The project's own file list is replaced by the
      // walk above, so what the oracle analysed is a property of this run and
      // not of whichever tsconfig a repository happens to make its default.
      options = {
        ...parsed.options,
        noEmit: true, skipLibCheck: true,
        declaration: false, composite: false, incremental: false,
      };
      configErrors += parsed.errors.filter((d) => d.category === ts.DiagnosticCategory.Error).length;
    }
  }

  const program = ts.createProgram({ rootNames: roots.map((f) => f.abs), options });
  const checker = program.getTypeChecker();

  const analysed = [];
  const sources = [];
  for (const f of roots) {
    const sf = program.getSourceFile(f.abs);
    if (!sf) continue;
    analysed.push(f.rel);
    sources.push({ sf, rel: f.rel });
  }
  const analysedSet = new Set(analysed);
  const loadErrors = roots.length - sources.length;

  // Every function declared in the analysed set. tsc has no notion of
  // reachability and does not need one: it type-checks every call site in every
  // function, so a function here is a function the oracle positively analysed.
  const reachable = new Set();
  const declLineOf = new Map();
  for (const { sf, rel: r } of sources) {
    const visit = (n) => {
      if (isNamedFunction(n)) {
        const line = declStart(declNode(n), sf);
        declLineOf.set(n, line);
        // A named function nested inside another named one is never a
        // caller key this oracle emits, so leaving it reachable would let
        // an arm that keys there be contradicted over granularity rather
        // than over a wrong edge.
        if (!namedEnclosing(n)) reachable.add(`${r}\u0000${line}`);
      }
      ts.forEachChild(n, visit);
    };
    ts.forEachChild(sf, visit);
  }

  // Why a call site went unresolved decides whether a rate computed from this
  // oracle is worth quoting: a site the checker cannot resolve withholds its
  // caller, and a large withheld set means the oracle is declining to speak
  // about most of the program. The two candidate causes look identical in the
  // totals and are told apart here, once, rather than argued about.
  const unresolvedBy = new Map();
  const unresolvedInDepFile = { yes: 0, no: 0 };
  const fileHasMissingDep = new Map();
  const missingDeps = new Set();
  const hasMissingDep = (sf) => {
    if (fileHasMissingDep.has(sf.fileName)) return fileHasMissingDep.get(sf.fileName);
    let missing = false;
    for (const st of sf.statements) {
      const spec = (ts.isImportDeclaration(st) || ts.isExportDeclaration(st)) ? st.moduleSpecifier : null;
      if (!spec || !ts.isStringLiteral(spec)) continue;
      if (spec.text.startsWith(".") || spec.text.startsWith("/")) continue;
      const r = ts.resolveModuleName(spec.text, sf.fileName, options, ts.sys);
      if (!r.resolvedModule) { missing = true; missingDeps.add(spec.text); }
    }
    fileHasMissingDep.set(sf.fileName, missing);
    return missing;
  };

  const counts = {
    call_sites: 0, resolved: 0, unresolved: 0, top_level: 0,
    external_target: 0, abstract_target: 0, emitted: 0, jsx_skipped: 0,
    functions: reachable.size, functions_withheld: 0,
  };
  const emitted = new Set();
  const lines = [];
  // Callers with at least one call site this checker could not take to an
  // implementation. Withheld from the reachable set, so an arm that resolved
  // that site concretely is not charged for being more precise than tsc.
  const withheld = new Set();
  const withhold = (site, sf, r) => {
    const fn = namedEnclosing(site);
    if (!fn) return;
    withheld.add(`${r}\u0000${declLineOf.get(fn) ?? declStart(declNode(fn), sf)}`);
  };

  for (const { sf, rel: r } of sources) {
    const visit = (n) => {
      if (ts.isJsxOpeningElement(n) || ts.isJsxSelfClosingElement(n)) counts.jsx_skipped++;
      if (ts.isCallExpression(n) || ts.isNewExpression(n) || ts.isTaggedTemplateExpression(n)) {
        counts.call_sites++;
        const sig = checker.getResolvedSignature(n);
        let decl = sig && sig.declaration && implementationOf(sig.declaration, checker);
        if (ts.isNewExpression(n)) decl = constructedClass(n, checker) || decl;
        if (!decl) {
          counts.unresolved++;
          withhold(n, sf, r);
          if (args.diagnose) {
            const callee = ts.isTaggedTemplateExpression(n) ? n.tag : n.expression;
            const t = callee.getText(sf).slice(0, 60);
            unresolvedBy.set(t, (unresolvedBy.get(t) || 0) + 1);
            unresolvedInDepFile[hasMissingDep(sf) ? "yes" : "no"]++;
          }
        } else {
          counts.resolved++;
          const tsf = decl.getSourceFile();
          const trel = rel(repo, tsf.fileName);
          if (!analysedSet.has(trel)) {
            // Out of scope on both sides: step 3 restricts every arm to the
            // analysed file set, so a call into `lib.d.ts` or a dependency is
            // dropped for everyone and its caller stays judgeable. Testing
            // this before the body test matters: nothing in a `.d.ts` has a
            // body, so the other order withholds every function that calls
            // `console.log`.
            counts.external_target++;
          } else if (!decl.body && !ts.isClassDeclaration(decl) && !ts.isClassExpression(decl)) {
            // A type alias with a call signature, an interface member, an
            // abstract member. Not a function, so not an edge, and the caller
            // stops being one this oracle can be held to.
            counts.abstract_target++;
            withhold(n, sf, r);
          } else {
            const fn = namedEnclosing(n);
            if (!fn) {
              counts.top_level++;
            } else {
              const callerLine = declLineOf.get(fn) ?? declStart(declNode(fn), sf);
              const calleeLine = declStart(declNode(decl), tsf);
              const key = `${r}\u0000${callerLine}\u0000${trel}\u0000${calleeLine}`;
              if (!emitted.has(key)) {
                emitted.add(key);
                counts.emitted++;
                lines.push(JSON.stringify({
                  caller_file: r,
                  caller_line: sf.getLineAndCharacterOfPosition(n.getStart(sf, false)).line + 1,
                  caller_decl_file: r,
                  caller_decl_line: callerLine,
                  caller_func: nameOf(fn),
                  callee_func: nameOf(decl),
                  callee_file: trel,
                  callee_line: calleeLine,
                  dynamic: false,
                }));
              }
            }
          }
        }
      }
      ts.forEachChild(n, visit);
    };
    ts.forEachChild(sf, visit);
  }

  const header = {
    _header: true,
    oracle: `typescript ${ts.version} checker`,
    algorithm: "tsc-resolved-signature",
    tests_included: args.tests,
    tsconfig: configPath ? rel(repo, configPath) : null,
    config_errors: configErrors,
    subdirs: args.subdirs,
    analysed_file_count: analysed.length,
    analysed_files: analysed,
    root_count: null,
    load_errors: loadErrors,
    counts,
  };
  for (const k of withheld) reachable.delete(k);
  counts.functions_withheld = withheld.size;
  counts.functions_judged = reachable.size;
  const reach = {
    _reachable: true,
    funcs: [...reachable].map((k) => { const [f, l] = k.split("\u0000"); return [f, Number(l)]; }),
  };

  fs.writeFileSync(
    args.out,
    [JSON.stringify(header), JSON.stringify(reach), ...lines].join("\n") + "\n",
    "utf-8",
  );
  if (args.diagnose) {
    console.error(`
unresolved sites in a file with an unresolvable bare import: ` +
      `${unresolvedInDepFile.yes}, elsewhere: ${unresolvedInDepFile.no}`);
    console.error(`unresolvable module specifiers: ${[...missingDeps].sort().join(", ") || "none"}`);
    console.error("top unresolved callee expressions:");
    for (const [t, c] of [...unresolvedBy].sort((a, b) => b[1] - a[1]).slice(0, 20)) {
      console.error(`  ${String(c).padStart(5)}  ${t}`);
    }
    console.error("");
  }
  console.error(`analysed ${analysed.length} files, ${reachable.size} functions`);
  console.error(JSON.stringify(counts, null, 2));
}

main();
