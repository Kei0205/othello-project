# Egaroucid Engine Setup

本プロジェクトは **純粋な Python による Alpha-Beta 探索エンジン** を内包しているため、追加バイナリなしでも対局・解析・戦術評価が完全に動作します。

世界最強クラスのオセロ探索エンジンである [Egaroucid (エガルシード)](https://github.com/Nyanyan/Egaroucid) と連携して超高速・高精度な完全読みを行いたい場合は、以下の手順で配置できます。

## Windows (SIMD版)
1. Egaroucid for Console を [公式リリースページ](https://github.com/Nyanyan/Egaroucid/releases) からダウンロード
2. 以下のパスに配置:
   ```
   egaroucid_console/Egaroucid_for_Console_7_8_1_Windows_SIMD/
     ├── Egaroucid_for_Console_7_8_1_SIMD.exe
     ├── msvcp140.dll
     └── resources/
           ├── book.egbk3
           ├── eval.egev2
           └── ...
   ```

## Linux / macOS
Linux / macOS 向けにビルドした実行ファイルを配置し、`engine.py` の `DEFAULT_EGAROUCID_PATH` または初期化時の引数 `cli_path` にバイナリのパスを指定してください。
バイナリが存在しない場合は自動的に組み込みの Alpha-Beta エンジンにフォールバックします。
