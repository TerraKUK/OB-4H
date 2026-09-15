"""Print detected Order Blocks using the same logic as the Telegram workflow."""

from pathlib import Path

import pandas as pd

from ob_detector import find_all_obs


RAW_DATA_DIR = Path("data/raw")


def main() -> None:
    files = sorted(RAW_DATA_DIR.glob("*_1d.csv"))
    if not files:
        raise SystemExit("No CSV files in data/raw/")

    total = 0
    for path in files:
        symbol = path.name.removesuffix("_1d.csv")
        frame = pd.read_csv(path, parse_dates=["ts"])
        if len(frame) < 205:
            print(f"\n=== {symbol}: skip (less than 205 candles) ===")
            continue

        blocks = find_all_obs(frame, symbol)
        print(f"\n=== {symbol}: {len(blocks)} OB ===")
        print(f"{'#':<3} {'OB date':<12} {'Type':<9} {'Bottom':<14} {'Top':<14} {'BOS date':<12} {'Score'}")
        print("-" * 82)
        for number, block in enumerate(blocks, start=1):
            print(
                f"{number:<3} {block.ob_time:<12} {block.direction.upper():<9} "
                f"{block.bottom:<14.8f} {block.top:<14.8f} {block.bos_time:<12} {block.score}/5"
            )
        total += len(blocks)

    print(f"\n{'=' * 60}\nTOTAL: {total} OB across {len(files)} pairs\n{'=' * 60}")


if __name__ == "__main__":
    main()
