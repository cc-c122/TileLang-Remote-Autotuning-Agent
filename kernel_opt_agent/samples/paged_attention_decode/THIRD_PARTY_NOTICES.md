# Third Party Notices

This directory vendors code from the TileLang project for the C500 Paged Attention
Decode baseline scaffold.

## TileLang Paged Attention Decode Sample

- Source repository: `tile-ai/tilelang`
- Source commit: `1d155f4b80865edfe0009ad952135b7afbd4f05a`
- Source file: `examples/blocksparse_attention/example_tilelang_sparse_gqa_decode_paged.py`
- Vendored file: `_upstream_sparse_gqa_decode_paged.py`
- Vendored SHA-256: `dca2e0114b88a029ec7e61e61cc791eaa97a4e2f5c4065cf3eef6b082c6a86c6`
- Modifications: header comments were added to identify source and retrieval date; no functional changes were made to the vendored source.

The helper file `heuristic.py` is vendored from the same repository path
`examples/blocksparse_attention/heuristic.py` at the same commit. Its LF-normalized
SHA-256 is `2c861d5b5a9c449745f63925353c6d98053c43222ae4c06745f6fa49afffd642`.

## License Text

```text
MIT License

Copyright (c) Tile-AI.
**During the period from December 1, 2024, to Mar 14, 2025, this project is
subject to additional collaboration terms with Microsoft Corporation.**

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE
```
