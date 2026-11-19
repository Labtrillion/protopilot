# Aligning live alarms with tep-sops

`server/sop_align.py` picks the SOP that `analyze_alarm` sends to Zoro. It reads the alarm and the
observed history kept by `Plant`. It replaces the tag-only `find_sop` lookup. The injected IDV vector
is never an input.

## Why the previous retrieval did not align

The causes fall into two groups: the SOP inputs do not match what the Plant gives (1), and the
retrieval method cannot use the SOP content it retrieves (2).

| # | Problem | Evidence |
|---|---|---|
| 1 | Tag spelling differs | tep-sops uses `additional_measurements[k]` and `XMV(n) actual`; the Plant uses `XMEASADD(k+1)` and `XMV(n):actual` |
| 1 | Valve positions were missing | The checkpoint held only `XMV(10):actual`. Nine IDV conditions need other valve positions |
| 1 | No history | The SOP conditions need persistence and shape; the checkpoint held only the cooling trend |
| 1 | Nominal envelopes were measured elsewhere | The source envelopes come from 0–2 h of one source run. On this Plant, fault-free signals leave them for much of a 9 h run: `XMEAS(15)` 70 %, `XMEAS(1)` 48 %, `XMEAS(18)` 31 % |
| 1 | Alarm coverage differs | The Plant alarms on the Downs & Vogel limits of `XMEAS(7, 8, 9, 12, 15)`. tep-sops has no sensor SOP for `XMEAS(9)` high or `XMEAS(8)`/`XMEAS(12)` high |
| 2 | Text retrieval cannot discriminate | One alarm tag, such as `XMEAS(15)` low, is shared by many IDVs. BM25 over the tag text ranked the true IDV SOP first at 0/60 and 8/61 alarms (dev seeds) and 2/83 (held-out seed) |
| 2 | The discriminating content was unused | The IDV SOPs carry 73 machine-readable conditions in 50 rules. BM25 reads them as words; it never evaluates them against observations |

## What changed

- **`plant.py`**: each checkpoint now carries the last 2 h of observations (`history`) and the
  actual positions of all 12 valves (`XMV(n):actual`).
- **`scripts/build_sop_index.py`**: builds `server/sop-index.json` from a tep-sops checkout. The
  index stores the IDV SOP conditions with their tags rewritten to Plant keys, together with the
  compact Zoro records from `idv-sops-zoro/` and `sensor-sops-zoro/`.
- **`scripts/calibrate_envelopes.py`**: repeats the SOPs' own "measured nominal envelope"
  procedure (min/max of fault-free observations) on this Plant. It uses seeds 11–13 over 0–9 h,
  disjoint from the evaluation seeds. It also covers the SOP mechanism signals. The source
  envelopes stay in the index.
- **`server/sop_align.py`** scores each IDV SOP as follows:
  - A condition holds when at least half of the last 0.25 h of samples violate it.
  - Each rule follows its own `all`/`any` logic.
  - A held rule scores 2 if it adds evidence beyond the active operating-limit alarm, otherwise 1.
  - A stated step or random-variation type that the history shape contradicts costs 1.
  - The fraction of the SOP's mechanism signals outside their live envelope adds up to 1.

  An IDV SOP is selected only with specific evidence and a strict lead over the next SOP. Otherwise
  the pipeline uses the sensor SOP that matches the tag, side and severity band, then the curated
  demo SOP.
- **`alarm_response.py`**: sends the selected SOP to Zoro, along with the held conditions and the
  two runner-up SOPs. It returns the full `alignment` in the result and the trace.

## Evaluation

Run `python scripts/eval_sop_align.py --bm25 <vendored zoro-rag>`. Each run injects IDV 0–28 at 1 h
and continues to 9 h on the headless Plant.

- **Dev seeds** 1431655765 and 20261119 were used while the scoring was designed.
- **Seed 777** was run once, after the scoring was frozen.

Results are in `scripts/eval_sop_align_{dev,heldout}.json`.

| Metric | Dev seeds (2) | Held-out seed 777 |
|---|---|---|
| Alarms with the true IDV SOP ranked first: aligner | 96/121 | 60/83 |
| Same metric: BM25 baseline | 8/121 | 2/83 |
| First alarm per faulted run, true IDV selected: aligner | 5/7 | 2/5 |
| Same metric: BM25 baseline | 1/7 | 1/5 |
| Fault-free run: an IDV SOP selected (alarms and probes) | 0 | 0 |
| On-demand probe at onset + 2 h: correct / wrong / abstained (of 28) | 13/5/10 and 11/4/13 | 8/7/13 |
| Alarms with a matching sensor SOP | all | all (only `XMEAS(15)` low fired) |

## Known limits

- **IDV-20 is over-selected.** Its envelope on separator pressure `XMEAS(13)` moves with most faults.
  This is the main error: IDV-01, -08 and -13 alarms on the held-out seed went to IDV-20.
- **Some SOP pairs share identical conditions.** IDV 14/17 and 15/18 do; IDV 3/9, 4/11 and 5/12
  differ only by the step-vs-random type, which a short window often cannot separate.
- **Most IDVs never raise an operating-limit alarm within 8 h of onset.** On those runs the
  alignment is exercised only by the on-demand probes.
- **The IDV compact SOPs expose only `hold`**, so a correct alignment does not by itself change
  the branches that are compared.
- The evaluation used 3 seeds and one 8 h horizon. The live envelopes come from 3 calibration seeds.
