import os
import pandas as pd


def find_regular_fractals(df):
    """Find 3-bar fractals."""
    df = df.copy()
    df["fractal_high"] = False
    df["fractal_low"] = False

    for i in range(1, len(df) - 1):
        curr_high = df["high"].iloc[i]
        curr_low = df["low"].iloc[i]

        if curr_high > df["high"].iloc[i - 1] and curr_high > df["high"].iloc[i + 1]:
            df.at[df.index[i], "fractal_high"] = True

        if curr_low < df["low"].iloc[i - 1] and curr_low < df["low"].iloc[i + 1]:
            df.at[df.index[i], "fractal_low"] = True

    return df


def find_breakouts(df):
    """
    Track last fractal high/low, mark when price breaks them by CLOSE.
    - Break of fractal high (close > last fractal high) -> bullish BOS
    - Break of fractal low  (close < last fractal low)  -> bearish BOS
    """
    df = df.copy()
    df["break_high"] = False
    df["break_low"] = False
    df["broken_high_level"] = None
    df["broken_low_level"] = None
    df["fractal_high_time"] = None
    df["fractal_low_time"] = None

    last_fractal_high = None
    last_fractal_high_time = None
    last_fractal_low = None
    last_fractal_low_time = None

    for i in range(len(df)):
        row = df.iloc[i]

        if row["fractal_high"]:
            last_fractal_high = row["high"]
            last_fractal_high_time = row["ts"]
        if row["fractal_low"]:
            last_fractal_low = row["low"]
            last_fractal_low_time = row["ts"]

        # Break of high (bullish BOS)
        if last_fractal_high is not None and row["close"] > last_fractal_high:
            df.at[df.index[i], "break_high"] = True
            df.at[df.index[i], "broken_high_level"] = last_fractal_high
            df.at[df.index[i], "fractal_high_time"] = last_fractal_high_time
            last_fractal_high = None
            last_fractal_high_time = None

        # Break of low (bearish BOS)
        if last_fractal_low is not None and row["close"] < last_fractal_low:
            df.at[df.index[i], "break_low"] = True
            df.at[df.index[i], "broken_low_level"] = last_fractal_low
            df.at[df.index[i], "fractal_low_time"] = last_fractal_low_time
            last_fractal_low = None
            last_fractal_low_time = None

    return df


if __name__ == "__main__":
    raw_dir = "data/raw"
    files = [f for f in os.listdir(raw_dir) if f.endswith(".csv")]
    if not files:
        print("No CSV files in data/raw/")
        exit(1)

    file_path = os.path.join(raw_dir, files[0])
    print(f"Loading: {file_path}")

    df = pd.read_csv(file_path, parse_dates=["ts"])
    print(f"Loaded {len(df)} candles")

    df = find_regular_fractals(df)
    df = find_breakouts(df)

    print(f"\n=== RESULTS ===")
    print(f"Bullish BOS (break of high): {df['break_high'].sum()}")
    print(f"Bearish BOS (break of low):  {df['break_low'].sum()}")

    print(f"\n=== LAST 15 BREAKOUTS ===")
    breaks = df[df["break_high"] | df["break_low"]].tail(15)
    for _, row in breaks.iterrows():
        if row["break_high"]:
            kind = "BULLISH BOS"
            level = row["broken_high_level"]
            fractal_time = row["fractal_high_time"]
        else:
            kind = "BEARISH BOS"
            level = row["broken_low_level"]
            fractal_time = row["fractal_low_time"]

        print(f"{row['ts']} | {kind} | broke {level} (fractal at {fractal_time}) | close={row['close']}")
