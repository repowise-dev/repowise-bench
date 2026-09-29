| repo | family | tier | findings | judged | precision | 95% CI | recall | ECE |
|---|---|---|---:|---:|---:|---|---:|---:|
| * | get_answer | * | 74 | 74 | 24.3% | 16.0%-35.2% | - | - |
| * | get_answer | high | 29 | 29 | 34.5% | 19.9%-52.7% | - | - |
| * | get_answer | low | 11 | 11 | 0.0% | 0.0%-25.9% | - | - |
| * | get_answer | medium | 34 | 34 | 23.5% | 12.4%-40.0% | - | - |
| * | get_answer.citation_resolves | * | 130 | 130 | 97.7% | 93.4%-99.2% | - | - |
| * | get_answer.not_confidently_wrong | * | 80 | 80 | 95.0% | 87.8%-98.0% | - | - |
| * | get_answer.trap_refused | * | 6 | 6 | 83.3% | 43.6%-97.0% | - | - |
| click | get_answer | * | 18 | 18 | 38.9% | 20.3%-61.4% | - | - |
| click | get_answer | high | 9 | 9 | 33.3% | 12.1%-64.6% | - | - |
| click | get_answer | low | 2 | 2 | 0.0% | 0.0%-65.8% | - | - |
| click | get_answer | medium | 7 | 7 | 57.1% | 25.0%-84.2% | - | - |
| click | get_answer.citation_resolves | * | 25 | 25 | 100.0% | 86.7%-100.0% | - | - |
| click | get_answer.not_confidently_wrong | * | 20 | 20 | 95.0% | 76.4%-99.1% | - | - |
| click | get_answer.trap_refused | * | 2 | 2 | 100.0% | 34.2%-100.0% | - | - |
| cobra | get_answer | * | 18 | 18 | 27.8% | 12.5%-50.9% | - | - |
| cobra | get_answer | high | 9 | 9 | 55.6% | 26.7%-81.1% | - | - |
| cobra | get_answer | low | 4 | 4 | 0.0% | 0.0%-49.0% | - | - |
| cobra | get_answer | medium | 5 | 5 | 0.0% | 0.0%-43.4% | - | - |
| cobra | get_answer.citation_resolves | * | 23 | 23 | 95.7% | 79.0%-99.2% | - | - |
| cobra | get_answer.not_confidently_wrong | * | 20 | 20 | 95.0% | 76.4%-99.1% | - | - |
| cobra | get_answer.trap_refused | * | 2 | 2 | 50.0% | 9.5%-90.5% | - | - |
| composio | get_answer | * | 19 | 19 | 10.5% | 2.9%-31.4% | - | - |
| composio | get_answer | high | 4 | 4 | 25.0% | 4.6%-69.9% | - | - |
| composio | get_answer | low | 4 | 4 | 0.0% | 0.0%-49.0% | - | - |
| composio | get_answer | medium | 11 | 11 | 9.1% | 1.6%-37.7% | - | - |
| composio | get_answer.citation_resolves | * | 47 | 47 | 95.7% | 85.8%-98.8% | - | - |
| composio | get_answer.not_confidently_wrong | * | 20 | 20 | 95.0% | 76.4%-99.1% | - | - |
| composio | get_answer.trap_refused | * | 1 | 1 | 100.0% | 20.7%-100.0% | - | - |
| httpx | get_answer | * | 19 | 19 | 21.1% | 8.5%-43.3% | - | - |
| httpx | get_answer | high | 7 | 7 | 14.3% | 2.6%-51.3% | - | - |
| httpx | get_answer | low | 1 | 1 | 0.0% | 0.0%-79.3% | - | - |
| httpx | get_answer | medium | 11 | 11 | 27.3% | 9.7%-56.6% | - | - |
| httpx | get_answer.citation_resolves | * | 35 | 35 | 100.0% | 90.1%-100.0% | - | - |
| httpx | get_answer.not_confidently_wrong | * | 20 | 20 | 95.0% | 76.4%-99.1% | - | - |
| httpx | get_answer.trap_refused | * | 1 | 1 | 100.0% | 20.7%-100.0% | - | - |

| threshold | repo | metric | value | bound | status |
|---|---|---|---:|---|---|
| get_answer | * | precision | 0.2432 | >= 0.85 | fail |
| get_answer | * | findings | 74 | >= 25 | pass |
| get_answer | click | precision | 0.3889 | >= 0.8 | fail |
| get_answer | cobra | precision | 0.2778 | >= 0.8 | fail |
| get_answer | composio | precision | 0.1053 | >= 0.8 | fail |
| get_answer | httpx | precision | 0.2105 | >= 0.8 | fail |
| get_answer.high | * | precision | 0.3448 | >= 0.95 | fail |
| get_answer.not_confidently_wrong | * | precision | 0.95 | >= 0.97 | fail |
| get_answer.trap_refused | * | precision | 0.8333 | >= 1.0 | fail |
| get_answer.citation_resolves | * | precision | 0.9769 | >= 0.95 | pass |
