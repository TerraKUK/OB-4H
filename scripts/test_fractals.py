import os
import pandas as pd


def find_regular_fractals(df):
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


def find_bw_fractals(df):
    df = df.copy()
    df["bw_fractal_high"] = False
    df["bw_fractal_low"] = False

    for i in range(2, len(df) - 2):
        h = df["high"].iloc[i]
        l = df["low"].iloc[i]

        if (h > df["high"].iloc[i - 2] and h > df["high"].iloc[i - 1]
                and h > df["high"].iloc[i + 1] and h > df["high"].iloc[i + 2]):
            df.at[df.index[i], "bw_fractal_high"] = True

        if (l < df["low"].iloc[i - 2] and l < df["low"].iloc[i - 1]
                and l < df["low"].iloc[i + 1] and l < df["low"].iloc[i + 2]):
            df.at[df.index[i], "bw_fractal_low"] = True

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
    print(f"First date: {df['ts'].iloc[0]}")
    print(f"Last date:  {df['ts'].iloc[-1]}")

    df = find_regular_fractals(df)
    df = find_bw_fractals(df)

    print(f"\n=== RESULTS ===")
    print(f"Regular fractal highs: {df['fractal_high'].sum()}")
    print(f"Regular fractal lows:  {df['fractal_low'].sum()}")
    print(f"BW fractal highs:      {df['bw_fractal_high'].sum()}")
    print(f"BW fractal lows:       {df['bw_fractal_low'].sum()}")

    print(f"\n=== LAST 10 FRACTALS ===")
    fractals = df[df["fractal_high"] | df["fractal_low"]].tail(10)
    for _, row in fractals.iterrows():
        kind = "HIGH" if row["fractal_high"] else "LOW"
        print(f"{row['ts']} | {kind} | high={row['high']} low={row['low']}")
