# Fixed course data attribution

This bundle contains the registered `formal-main-course` and `development-reviewed-v1` resource sets from finance-RAG. It packages the same frozen inputs, evidence, evaluation answers, reviewed development assets and encoder bytes. Questions, answers, cohort membership, evidence and model files are not rewritten. The formal resource manifest is the registered `main_only` / `automatic_course` manifest, rather than the unqualified full candidate at `data/formal-v1/resources.json`.

## TAT-DQA

- Authors: Fengbin Zhu, Wenqiang Lei, Fuli Feng, Chao Wang, Haozhou Zhang and Tat-Seng Chua; NExT++ / TAT@NExT.
- Paper: *Towards complex document understanding by discrete reasoning*, ACM Multimedia 2022, pp. 4857–4866.
- Source: [official dataset](https://huggingface.co/datasets/next-tat/TAT-DQA), revision `bc46208b22f0bfe47152c98fd0938d238ce868c2`; [official project](https://nextplusplus.github.io/TAT-DQA/) and [repository](https://github.com/NExTplusplus/TAT-DQA).
- License: [Creative Commons Attribution 4.0 International](https://creativecommons.org/licenses/by/4.0/), as stated by the publisher's current dataset page and registered source manifest, checked 2026-10-27. Full license text is included in `CC-BY-4.0.txt`.
- Existing project processing: a frozen course subset, serialized questions and gold, PDF/OCR evidence, cropped regions, cohort assignments and reviewed development structures. This release preserves those existing files byte for byte and adds a portable inventory and installation manifests. It does not infer new answers or add a new experiment.
- The historical paper abstract used non-commercial wording; the bundle records the current official dataset release's CC BY 4.0 statement. It does not assert ownership over the underlying corporate report content.

## all-MiniLM-L6-v2

- Provider: [Sentence Transformers](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2), revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`.
- License: Apache License 2.0, stated by the pinned model card and `configs/formal/minilm-files.json`; checked 2026-10-27. Full license text is included in `APACHE-2.0.txt`.
- The original model card is retained at `models/all-MiniLM-L6-v2/README.md` within each resource root. Weights, tokenizer and configuration bytes are unchanged.
- Duplicate files are stored once per SHA256 in the transport archive and materialized at their original manifest-relative paths on installation.
