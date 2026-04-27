# Holdout Eval: 湛江中纸高浓磨机

## Run Summary

- Source holdout: `private_samples/real_proposals/上电湛江中纸高浓磨机项目成套方案VerA.pdf`
- Project ID: `aed0611f-80b1-4988-8c42-51ad08113fa7`
- Requirement card: `fdbc5fee-78ca-4e2f-9186-731962a6cdfb`
- Evidence bundle: `87bcc007-a700-4892-97c8-468cef61fcd6`
- Outline: `6345c539-f029-4896-828b-39d1965e77f1`
- Generated draft: `output/holdout-eval-zhanjiang-cached-generated-draft.md`

This holdout is a better generalization check than the Linyi holdout. Linyi is close to the Baoshan LCI steel blower sample. Zhanjiang is still in the LCI / high-voltage motor soft-start family, but the application is paper-mill high-consistency refiner equipment, so it is not a near-duplicate of the current strongest pilot sample.

## Runtime Notes

- Direct upload parse failed because `.env` requested `PARSER_BACKEND=docling`, but docling is not installed in the local backend venv.
- First evidence retrieval failed because `.env` requested `EMBEDDING_BACKEND=sentence-transformers`, but sentence-transformers is not installed.
- The completed run used existing cached parsed holdout content as the RFP input and restarted backend with temporary runtime overrides:
  - `PARSER_BACKEND=auto`
  - `EMBEDDING_BACKEND=auto`
- Because embedding fell back locally, this is not a full-quality dense retrieval evaluation. It is still useful for checking chain behavior, evidence contamination, outline generation, and section composition.

## Timing

- Evidence retrieval: about 1.1 seconds
- Outline generation: about 46 seconds
- Section generation: about 4 minutes 52 seconds for 15 sections
- Section generation used concurrency `4`, fast coherence pass enabled, quality gate skipped.

## Positive Signals

- The generated outline is structurally relevant: environment, power supply, supply scope, LCI architecture, start/sync logic, LCU, motor/load parameters, converter, transformers, switchgear, excitation, protection, commissioning, training/document delivery.
- No direct self-copy from the Zhanjiang holdout entered historical evidence; current `case_library` does not include `vera-46268861`.
- Main generated text mostly stays in the Zhanjiang / paper / high-consistency refiner / LCI soft-start context.
- Key parameters such as `10kV`, `50Hz`, `500/240MVA`, `4208kW`, `69s`, `29s`, `ICB/SCB/RCB/PT1/PT2`, and `PACSystems RX3i CPE305` were carried into the draft.

## Problems Found

1. Environment and embedding dependencies are not production-ready.
   - With current `.env`, direct upload and retrieval fail on this machine unless docling and sentence-transformers are installed or config is changed to `auto`.

2. Top-level evidence is only moderately relevant.
   - Evidence retrieval selected mainly `099-230101-001西安陕鼓（秦风气体）技术协议.pdf`.
   - That source is useful for medium-voltage VFD/interface patterns, but it is not a close LCI paper-refiner match.
   - This confirms the holdout is a genuine generalization test and also exposes that the current main library lacks a close equivalent.

3. Supply scope is materially wrong.
   - The generated supply chapter says only the LCI soft-start装置 is in the main supply table and marks motor, excitation, cooling, oil station, switchgear, input/output transformers as not default included.
   - The extracted requirement explicitly included these as part of the complete package. This is a serious requirement preservation failure.

4. Asset recommendation is still unsafe.
   - Several recommended assets have negative or near-zero scores.
   - Examples: load/start-curve figures suggested for switchgear, air-compressor/booster figures suggested for excitation.
   - Asset recommendations need a hard minimum score and section/equipment compatibility gate.

5. Some generated content still contains source residue or weak text.
   - `对标资料` appears in the environment chapter.
   - `鼓风机配套站房` appears in a paper/refiner project.
   - `参见 ）` dangling reference appears in the power chapter.
   - The training/document chapter starts by repeating DCS interface content before getting to training.

6. Hallucinated or over-specific engineering values appear.
   - Examples include switchgear dimensions, IAC-AFLR, JDZX10-10, 31.5kA/4s, SIL2, transformer impedance and thermal details.
   - Some may be plausible but are not clearly grounded in the extracted Zhanjiang requirement.

## Initial Judgment

The run is directionally better than the earlier badly off-topic drafts: the main body mostly stays on the right product family and project context. However, it is not yet acceptable for MVP demonstration without manual review, mainly due to supply-scope loss, unsafe asset recommendations, and ungrounded parameter invention.

The most important next fix is not more synonym hard-coding. It is stronger requirement preservation and section-level grounding:

- treat extracted supply scope as a hard constraint;
- reject retrieved blocks/assets that contradict hard constraints;
- filter negative-score assets;
- prevent peripheral source sections from driving unrelated chapters;
- run a lightweight final consistency pass against the requirement card before showing the draft.
