# Verified local pretrained sentence encoder

## What was run

On 2026-10-15, `sentence-transformers/all-MiniLM-L6-v2` was downloaded from its
official repository and run locally on CPU. It is a pretrained sentence-vector
encoder, not an answer-generating model. No fine-tuning, gradient updates,
generator inference, paid API, or user-data upload was performed during this
provisioning step.

- Repository: [official model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
- Pinned revision: `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`
- Revision source: [repository commit](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/commit/1110a243fdf4706b3f48f1d95db1a4f5529b4d41)
- License: Apache-2.0, as declared by that revision's model card;
  [license text](https://www.apache.org/licenses/LICENSE-2.0)
- Actual loaded parameters: 22,713,216; output dimensions: 384
- Pooling: attention-mask-aware mean token pooling, then normalization
- Effective maximum sequence length: 256 tokens including special tokens
- Weight format: `model.safetensors`; no pickle weights, remote model code,
  ONNX exports, OpenVINO exports, training scripts, or duplicate weight formats
- Eleven downloaded files: 91,578,415 bytes total
- Weight-file SHA-256:
  `53aa51172d142c89d9012cce15ae4d6cc0ca6895895114379cacb4fab128d9db`
- Adapter's full directory content SHA-256:
  `56f029cefc20ca92d25db372363d5880c656ca6cc3bc7897f0fb37e6895d72e8`

The per-file hashes, byte counts, and pinned download URLs are recorded in
`artifacts/encoder-provisioning/model-manifest.json`. The weight hash also
matches the upstream Hugging Face LFS SHA-256. Model weights and the Python
environment are outside this deliverable directory and are not packaged.

## Verified cloud paths and runtime

- Python: `/workspace/shared/rag-encoder-venv/bin/python`
- Model: `/workspace/shared/rag-models/all-MiniLM-L6-v2-1110a243fdf4`
- Python 3.12.14, Linux x86_64
- `sentence-transformers==6.1.0`
- `transformers==5.19.0`
- `torch==2.14.1+cpu`
- `safetensors==0.8.0`
- `huggingface-hub==1.33.0`
- `tokenizers==0.23.3`

The runtime was created as a separate `venv --system-site-packages`, reusing
already installed numerical libraries. Newly added wheels came from PyPI and
the official PyTorch CPU index; their exact URLs and verified hashes appear in
`artifacts/encoder-provisioning/added-wheels.lock`. PyTorch's wheel was about
196 MB. No CUDA runtime or GPU packages were installed; `torch.version.cuda`
is `None`. `pip check` passed.

`requirements-encoder.txt` records the complete installed dependency closure,
including inherited packages, for CPython 3.12 on Linux x86_64. It is separate
from the base/offline requirements. Other operating systems or architectures
may require the official platform-specific CPU wheel. No claim is made that
this Linux lock has been tested on those platforms.

## Reproduce provisioning

Provisioning is the only step below that uses the network. It downloads public
packages and public model files, never questions, evidence, answers, or labels.
Run these commands from the `rag-pilot` directory. Choose storage outside this
directory so the runtime and weights stay outside the deliverable archive.

```sh
python3.12 -m venv ../rag-encoder-venv
../rag-encoder-venv/bin/python -m pip install -r requirements-encoder.txt
../rag-encoder-venv/bin/python -m pip check
python3.12 docs/provision_encoder.py \
  --model-dir ../rag-models/all-MiniLM-L6-v2-1110a243fdf4
```

The complete fresh-venv recipe above records the verified version set; the
actual cloud installation reused system packages as noted above. The model
provisioner was also rerun against the local files to check every hash without
redownloading. It refuses mismatched existing files and partial downloads,
rather than silently replacing them or falling back to another source.

## Offline smoke test and use

```sh
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1 TOKENIZERS_PARALLELISM=false OMP_NUM_THREADS=2
export CUDA_VISIBLE_DEVICES=""
../rag-encoder-venv/bin/python docs/verify_encoder.py \
  --model-path ../rag-models/all-MiniLM-L6-v2-1110a243fdf4 \
  --output artifacts/encoder-provisioning/smoke-test.json
```

The smoke test loads only local files with `trust_remote_code=False`, forces
safetensors for its direct load, and tests the actual `ragpilot.retrieval.retrieve`
adapter. Its Python socket guard raises on connection attempts. All inputs are
neutral synthetic sentences; no pilot questions or gold labels are opened.

The recorded checks passed:

- Four finite float32 vectors with shape `(4, 384)` and unit norms
- Identical sentence gives an identical embedding within `1e-6`
- Cat/couch paraphrase cosine about 0.736; unrelated rocket sentence about 0.058
- Actual adapter returns `status="ok"` and ranks the paraphrase first
- CPU device, no CUDA runtime, zero Python socket connection attempts

These are installation/adapter smoke checks, not retrieval quality results.
The complete captured evidence is `artifacts/encoder-provisioning/smoke-test.json`,
including source-code hash, package versions, offline flags, timings, embedding
hash, candidate scores, and model fingerprint. Small floating-point differences
may occur on a different CPU or runtime.

For a retrieval configuration use `retriever: sentence_transformers`, an existing
absolute `model_path`, `max_seq_length: 256`, and `batch_size: 8`. Keep the offline
environment variables above. The adapter does not resolve Hub IDs or download
models. It returns an explicit unavailable result if local provisioning fails.
Long input is subject to the recorded token cap; retained evidence text and
encoder tokenization/truncation must be distinguished when reporting results.

## Primary documentation

- [Sentence Transformers installation](https://www.sbert.net/docs/installation.html)
- [SentenceTransformer API](https://sbert.net/docs/package_reference/sentence_transformer/model.html)
- [Official PyTorch CPU wheels](https://download.pytorch.org/whl/cpu/torch/)
- [Sentence Transformers 6.1.0 release](https://pypi.org/project/sentence-transformers/6.1.0/)
