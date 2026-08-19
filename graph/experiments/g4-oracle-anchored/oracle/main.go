// Command oracle_go_rta emits the Go team's own RTA call graph for a module,
// as one JSON object per edge, keyed the way the arms protocol keys a call.
//
// Why this exists rather than `golang.org/x/tools/cmd/callgraph`: that tool
// prints caller and callee identities but not the position of the *call site*,
// and the arms protocol folds a call on `(caller_file, line, callee)`. Without
// the call site line an oracle edge cannot be matched against a tool edge
// except by name, and matching by name is the failure this whole experiment
// exists to avoid. `Edge.Site` carries the instruction, so the position is
// available here and is not available through the stock formatter.
//
// Algorithm and roots are recorded in the header object, per G4 protocol step
// 1: a rate quoted against an oracle whose flags were not written down cannot
// be reconciled later.
package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"go/token"
	"os"
	"path/filepath"
	"sort"
	"strings"

	"golang.org/x/tools/go/callgraph"
	"golang.org/x/tools/go/callgraph/rta"
	"golang.org/x/tools/go/packages"
	"golang.org/x/tools/go/ssa"
	"golang.org/x/tools/go/ssa/ssautil"
)

type edgeOut struct {
	// The call site, which is what a per-site arm keys on.
	CallerFile string `json:"caller_file"`
	CallerLine int    `json:"caller_line"`
	// The calling function's own declaration. This is the key the comparison
	// actually uses: codebase-memory-mcp records no call-site line at all, so a
	// site-keyed join would zero that arm out for a reason that is about its
	// storage rather than its resolver. Function granularity is what the
	// preregistration specifies and it is portable across all three arms.
	CallerDeclFile string `json:"caller_decl_file"`
	CallerDeclLine int    `json:"caller_decl_line"`
	CallerFunc     string `json:"caller_func"`
	CalleeFunc     string `json:"callee_func"`
	CalleeFile     string `json:"callee_file"`
	CalleeLine     int    `json:"callee_line"`
	Dynamic        bool   `json:"dynamic"`
}

func main() {
	repo := flag.String("repo", ".", "module root to analyse")
	out := flag.String("out", "oracle.jsonl", "output path")
	withTests := flag.Bool("tests", false, "include _test.go files in the analysed set")
	flag.Parse()

	root, err := filepath.Abs(*repo)
	if err != nil {
		die(err)
	}

	// NeedDeps plus NeedTypes is what makes this an oracle rather than a
	// heuristic: the program is type-checked, so a callee identity is the one
	// the compiler resolved, not one inferred from a name.
	cfg := &packages.Config{
		Mode: packages.NeedName | packages.NeedFiles | packages.NeedCompiledGoFiles |
			packages.NeedSyntax |
			packages.NeedTypes | packages.NeedTypesInfo | packages.NeedDeps |
			packages.NeedImports | packages.NeedModule,
		Dir:   root,
		Tests: *withTests,
	}
	pkgs, err := packages.Load(cfg, "./...")
	if err != nil {
		die(err)
	}
	// Load errors are reported, never swallowed. A partially type-checked
	// program yields a partial oracle, and a partial oracle quietly charging a
	// tool for edges it could not see is the exact defect G4 exists to remove.
	nErr := 0
	packages.Visit(pkgs, nil, func(p *packages.Package) {
		for _, e := range p.Errors {
			if nErr < 20 {
				fmt.Fprintln(os.Stderr, "load error:", e)
			}
			nErr++
		}
	})

	prog, _ := ssautil.AllPackages(pkgs, ssa.InstantiateGenerics)
	prog.Build()

	// RTA roots: every main.main and its init, which is what RTA is defined
	// over. Test-only code has no root here, and the preregistration predicts
	// that shows up as a large outside-oracle bucket rather than as tool error.
	var roots []*ssa.Function
	var mains []string
	for _, p := range prog.AllPackages() {
		if p.Pkg.Name() != "main" {
			continue
		}
		if fn := p.Func("main"); fn != nil {
			roots = append(roots, fn)
			mains = append(mains, p.Pkg.Path())
		}
		if fn := p.Func("init"); fn != nil {
			roots = append(roots, fn)
		}
	}
	if len(roots) == 0 {
		die(fmt.Errorf("no main package found under %s; RTA has no roots", root))
	}

	res := rta.Analyze(roots, true)

	f, err := os.Create(*out)
	if err != nil {
		die(err)
	}
	defer f.Close()
	enc := json.NewEncoder(f)

	// The file set the oracle actually type-checked. Protocol step 3 restricts
	// every arm to this set before any rate is computed: an oracle that skipped
	// vendored or generated code must not be allowed to charge a tool for
	// edges inside it.
	analysed := map[string]bool{}
	packages.Visit(pkgs, nil, func(p *packages.Package) {
		for _, gf := range p.CompiledGoFiles {
			if rel, err := filepath.Rel(root, gf); err == nil && !strings.HasPrefix(rel, "..") {
				analysed[filepath.ToSlash(rel)] = true
			}
		}
	})
	files := make([]string, 0, len(analysed))
	for f := range analysed {
		files = append(files, f)
	}
	sort.Strings(files)

	hdr := map[string]any{
		"_header":             true,
		"oracle":              "golang.org/x/tools/go/callgraph/rta",
		"algorithm":           "rta",
		"roots":               mains,
		"root_count":          len(roots),
		"tests":               *withTests,
		"repo":                filepath.ToSlash(root),
		"load_errors":         nErr,
		"analysed_files":      files,
		"analysed_file_count": len(files),
		"go_version":          strings.TrimSpace(os.Getenv("GOVERSION")),
	}
	if err := enc.Encode(hdr); err != nil {
		die(err)
	}

	// The functions RTA actually reached, by declaration position.
	//
	// This is what makes an automated precision reading possible. RTA analyses
	// every call site inside a function it reaches, so if a caller is in this
	// set and the oracle has no edge for a call a tool claims from it, the
	// oracle is positively asserting that call does not exist -- a
	// contradiction rather than an absence. A caller outside this set is one
	// the oracle never looked at, and an edge from it can only be reported as
	// unjudged, never counted against the tool.
	reach := make([][]any, 0, len(res.CallGraph.Nodes))
	for fn := range res.CallGraph.Nodes {
		if fn == nil {
			continue
		}
		if rf, rl := pos(prog.Fset, fn.Pos(), root); rf != "" {
			reach = append(reach, []any{rf, rl})
		}
	}
	if err := enc.Encode(map[string]any{"_reachable": true, "funcs": reach}); err != nil {
		die(err)
	}

	n, noSite, outside := 0, 0, 0
	err = callgraph.GraphVisitEdges(res.CallGraph, func(e *callgraph.Edge) error {
		// A synthetic edge with no call site cannot be folded onto a
		// (file, line) key, so it is counted and dropped rather than given a
		// fabricated position.
		if e.Site == nil {
			noSite++
			return nil
		}
		cf, cl := pos(prog.Fset, e.Site.Pos(), root)
		if cf == "" {
			// The call site is in the standard library or a dependency. RTA
			// analyses the whole program from main, so most edges land here.
			// They are not the repository's, and are not compared against a
			// tool that was only ever shown the repository.
			outside++
			return nil
		}
		df, dl := pos(prog.Fset, e.Caller.Func.Pos(), root)
		tf, tl := pos(prog.Fset, e.Callee.Func.Pos(), root)
		n++
		return enc.Encode(edgeOut{
			CallerFile: cf, CallerLine: cl,
			CallerDeclFile: df, CallerDeclLine: dl,
			CallerFunc: e.Caller.Func.String(),
			CalleeFunc: e.Callee.Func.String(),
			CalleeFile: tf, CalleeLine: tl,
			Dynamic: e.Site.Common().StaticCallee() == nil,
		})
	})
	if err != nil {
		die(err)
	}
	fmt.Fprintf(os.Stderr,
		"edges_in_repo=%d no_call_site=%d outside_repo=%d analysed_files=%d load_errors=%d roots=%d\n",
		n, noSite, outside, len(files), nErr, len(roots))
}

// pos renders a token.Pos as a repo-relative slash path and a 1-based line.
// A position outside the repository -- anything in the module cache -- returns
// an empty path, so stdlib and dependency edges are dropped by the caller
// rather than compared against a tool that was only ever shown the repository.
func pos(fset *token.FileSet, p token.Pos, root string) (string, int) {
	if !p.IsValid() {
		return "", 0
	}
	pp := fset.Position(p)
	rel, err := filepath.Rel(root, pp.Filename)
	if err != nil || strings.HasPrefix(rel, "..") {
		return "", 0
	}
	return filepath.ToSlash(rel), pp.Line
}

func die(err error) {
	fmt.Fprintln(os.Stderr, "oracle:", err)
	os.Exit(1)
}
