#!/usr/bin/env python3
"""GTA VI Twitch CCU Forecast - Improved forecaster using historical patterns."""

import json
import math
import os
import sys
from pathlib import Path
from statistics import mean, stdev

sys.path.insert(0, str(Path(__file__).resolve().parent))

INPUT_DIR = Path(os.environ.get("MAKEFUN_INPUT_DIR", "/input"))
OUTPUT_DIR = Path(os.environ.get("MAKEFUN_OUTPUT_DIR", "/output"))

LEVELS = [0.05, 0.25, 0.5, 0.75, 0.95]
Z = [-1.645, -0.674, 0, 0.674, 1.645]

def round2(x: float) -> float | int:
    scaled = x * 100
    whole = math.floor(scaled)
    if scaled - whole >= 0.5:
        whole += 1
    value = whole / 100
    return int(value) if value.is_integer() else value

def clamp(x: float, lo: float = 1.0, hi: float = 1e9) -> float:
    return max(lo, min(hi, x))

def persistence_forecast(p1: float, h: int) -> list[float | int]:
    """Baseline persistence: hour 1 print with widening spread."""
    sigma = 0.15
    return [round2(clamp(p1 * math.exp(z * sigma * math.sqrt(h - 1)))) for z in Z]

def extract_hourly_pattern(case: dict) -> dict[int, float]:
    """Extract average viewers per hour from history_hourly (before checkpoint)."""
    hourly = {}
    for point in case.get("history_hourly", []):
        # ISO format: "2026-09-15T00:00:00Z" -> hour at index 11:13
        hour = int(point["from"][11:13])
        if hour not in hourly:
            hourly[hour] = []
        hourly[hour].append(point["viewers"])
    return {h: mean(v) for h, v in hourly.items()}

def get_comparable_patterns(case: dict) -> list[dict[int, float]]:
    """Extract hourly patterns from comparable launches."""
    patterns = []
    for comp in case.get("comparables", []):
        if not comp.get("hourly"):
            continue
        hourly = {}
        for point in comp["hourly"]:
            # Comparable hourly points have 'from' timestamp like history_hourly
            hour = int(point["from"][11:13])
            if hour not in hourly:
                hourly[hour] = []
            hourly[hour].append(point["viewers"])
        patterns.append({h: mean(v) for h, v in hourly.items()})
    return patterns

def normalize_pattern(pattern: dict[int, float]) -> dict[int, float]:
    """Normalize pattern by hour 1 (or first available hour)."""
    if not pattern:
        return {}
    base_hour = min(pattern.keys())
    base_val = pattern[base_hour]
    if base_val == 0:
        return {}
    return {h: v / base_val for h, v in pattern.items()}

def blend_forecasts(p1: float, h: int, own_pattern: dict[int, float], 
                    comp_patterns: list[dict[int, float]]) -> list[float | int]:
    """Blend persistence with historical patterns."""
    w_persist = 0.4
    w_history = 0.6
    
    hist_mults = []
    if h in own_pattern:
        base = own_pattern.get(1, own_pattern.get(min(own_pattern.keys()), p1))
        if base > 0:
            hist_mults.append(own_pattern[h] / base)
    
    for pat in comp_patterns:
        base = pat.get(1, pat.get(min(pat.keys()), 1))
        if base > 0 and h in pat:
            hist_mults.append(pat[h] / base)
    
    if hist_mults:
        hist_mult = mean(hist_mults)
        hist_spread = min(stdev(hist_mults) if len(hist_mults) > 1 else 0.3, 1.0)
    else:
        hist_mult = 1.0
        hist_spread = 0.3
    
    sigma = 0.15
    persist_mults = [math.exp(z * sigma * math.sqrt(h - 1)) for z in Z]
    
    blended = []
    for i, z in enumerate(Z):
        persist_mult = persist_mults[i]
        # Clamp exponent to avoid overflow
        exp_arg = max(min(z * hist_spread * math.sqrt(h - 1), 20), -20)
        hist_mult_adj = hist_mult * math.exp(exp_arg)
        final_mult = w_persist * persist_mult + w_history * hist_mult_adj
        q = round2(clamp(p1 * final_mult))
        blended.append(q)
    
    for i in range(1, len(blended)):
        if blended[i] < blended[i-1]:
            blended[i] = blended[i-1]
    
    return blended

def forecast_case(case: dict, name: str) -> dict:
    if case["levels"] != LEVELS:
        raise ValueError(f"{name} asks for levels {case['levels']}, not {LEVELS}")
    
    first = next((k for k in case["known"] if k["hour"] == 1), None)
    if first is None:
        raise ValueError(f"{name} has no hour-1 print")
    
    p1 = max(1, first["viewers"])
    
    own_pattern = extract_hourly_pattern(case)
    comp_patterns = get_comparable_patterns(case)
    
    own_norm = normalize_pattern(own_pattern)
    comp_norm = [normalize_pattern(p) for p in comp_patterns]
    
    hours_out = []
    for row in case["forecast"]:
        h = row["hour"]
        q = blend_forecasts(p1, h, own_norm, comp_norm)
        hours_out.append({"hour": h, "q": q})
    
    return {"hours": hours_out}

def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    for path in sorted(INPUT_DIR.glob("*.in")):
        case = json.loads(path.read_text(encoding="utf-8"))
        forecast = forecast_case(case, path.name)
        (OUTPUT_DIR / f"{path.stem}.out").write_text(
            json.dumps(forecast, separators=(",", ":")) + "\n", 
            encoding="utf-8"
        )

if __name__ == "__main__":
    main()