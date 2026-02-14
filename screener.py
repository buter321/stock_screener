#!/usr/bin/env python3
"""
Automated Stock Screener for IDX
Runs via GitHub Actions, writes to Google Sheets
"""

import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime
import json
import os
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
import time

# ============================================
# CONFIGURATION - EDIT TICKER LIST DI SINI
# ============================================

TICKER_STRING = 'BBCA,BBRI,BMRI,TLKM,ASII,UNVR,INDF,KLBF,ICBP,SMGR'

# Jangan edit di bawah ini
TICKERS = [ticker.strip() + '.JK' for ticker in TICKER_STRING.split(',') if ticker.strip()]

# ============================================

SPREADSHEET_ID = os.environ.get('SPREADSHEET_ID')
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']

# ============================================
# DATA COLLECTION
# ============================================

def get_comprehensive_data(ticker):
    try:
        print(f"Processing {ticker}...", end=" ", flush=True)
        
        # Add delay to avoid rate limiting
        time.sleep(2)
        
        # Download with user agent
        stock = yf.Ticker(ticker)
        
        # Try to get historical data with retry
        hist = None
        for attempt in range(3):
            try:
                hist = stock.history(period="3mo")
                if len(hist) >= 50:
                    break
                time.sleep(3)
            except Exception as e:
                print(f"Attempt {attempt+1} failed: {e}", end=" ")
                time.sleep(5)
        
        if hist is None or len(hist) < 50:
            print("❌ Insufficient data")
            return None
        
        # Get info with error handling
        try:
            info = stock.info
        except:
            print("❌ Failed to get info")
            return None
        
        pe_ratio = info.get('trailingPE', np.nan)
        pb_ratio = info.get('priceToBook', np.nan)
        roe = info.get('returnOnEquity', np.nan) * 100 if info.get('returnOnEquity') else np.nan
        profit_margin = info.get('profitMargins', np.nan) * 100 if info.get('profitMargins') else np.nan
        debt_to_equity = info.get('debtToEquity', np.nan) / 100 if info.get('debtToEquity') else np.nan
        market_cap = info.get('marketCap', np.nan)
        
        current_price = hist['Close'].iloc[-1]
        high_52w = hist['High'].max()
        low_52w = hist['Low'].min()
        pct_from_high = ((current_price - high_52w) / high_52w) * 100
        pct_from_low = ((current_price - low_52w) / low_52w) * 100
        
        technical = calculate_technical_indicators(hist)
        bandar = calculate_bandarmology_proxies(hist)
        
        print("✅")
        
        return {
            'Ticker': ticker.replace('.JK', ''),
            'Date': datetime.now().strftime('%Y-%m-%d'),
            'Price': round(current_price, 2),
            'Volume': int(hist['Volume'].iloc[-1]),
            'Market_Cap': int(market_cap) if not np.isnan(market_cap) else 'N/A',
            'PE': round(pe_ratio, 2) if not np.isnan(pe_ratio) else 'N/A',
            'PB': round(pb_ratio, 2) if not np.isnan(pb_ratio) else 'N/A',
            'ROE': round(roe, 2) if not np.isnan(roe) else 'N/A',
            'Net_Margin': round(profit_margin, 2) if not np.isnan(profit_margin) else 'N/A',
            'DER': round(debt_to_equity, 2) if not np.isnan(debt_to_equity) else 'N/A',
            'SMA_20': technical['sma20'],
            'SMA_50': technical['sma50'],
            'RSI': technical['rsi'],
            'Vol_Ratio': technical['vol_ratio'],
            'Price_Change_5D': technical['price_change_5d'],
            'Above_SMA20': current_price > technical['sma20'],
            'Above_SMA50': current_price > technical['sma50'],
            'Accum_Signal': bandar['accum_signal'],
            'MFI': bandar['mfi'],
            'OBV_Trend': bandar['obv_trend'],
            'PV_Divergence': bandar['pv_divergence'],
            'Pct_From_High': round(pct_from_high, 2),
            'Pct_From_Low': round(pct_from_low, 2),
        }
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return None


def calculate_technical_indicators(hist):
    closes = hist['Close'].values
    volumes = hist['Volume'].values
    
    sma_20 = np.mean(closes[-20:])
    sma_50 = np.mean(closes[-50:])
    rsi = calculate_rsi(closes, 14)
    
    avg_vol_20d = np.mean(volumes[-20:])
    current_vol = volumes[-1]
    vol_ratio = current_vol / avg_vol_20d if avg_vol_20d > 0 else 1
    
    price_change_5d = ((closes[-1] - closes[-6]) / closes[-6]) * 100
    
    return {
        'sma20': round(sma_20, 2),
        'sma50': round(sma_50, 2),
        'rsi': round(rsi, 2),
        'vol_ratio': round(vol_ratio, 2),
        'price_change_5d': round(price_change_5d, 2)
    }


def calculate_rsi(prices, period=14):
    deltas = np.diff(prices)
    seed = deltas[:period+1]
    up = seed[seed >= 0].sum() / period
    down = -seed[seed < 0].sum() / period
    
    if down == 0:
        return 100
    
    rs = up / down
    rsi = 100 - (100 / (1 + rs))
    return rsi


def calculate_bandarmology_proxies(hist):
    closes = hist['Close'].values
    highs = hist['High'].values
    lows = hist['Low'].values
    volumes = hist['Volume'].values
    
    avg_vol_20d = np.mean(volumes[-20:])
    recent_vol_spikes = sum(1 for v in volumes[-5:] if v > avg_vol_20d * 1.5)
    price_change_5d = ((closes[-1] - closes[-6]) / closes[-6]) * 100
    
    if recent_vol_spikes >= 3 and price_change_5d > -3:
        accum_signal = 'STRONG_ACCUM'
    elif recent_vol_spikes >= 2 and price_change_5d > 0:
        accum_signal = 'ACCUM'
    elif recent_vol_spikes >= 3 and price_change_5d < -5:
        accum_signal = 'DISTRIB'
    else:
        accum_signal = 'NEUTRAL'
    
    mfi = calculate_mfi(highs, lows, closes, volumes, 14)
    obv = calculate_obv(closes, volumes)
    obv_trend = 'UP' if obv[-1] > obv[-21] else 'DOWN'
    
    price_trend = 'UP' if closes[-1] > closes[-21] else 'DOWN'
    pv_divergence = (obv_trend == 'UP' and price_trend == 'DOWN')
    
    return {
        'accum_signal': accum_signal,
        'mfi': round(mfi, 2),
        'obv_trend': obv_trend,
        'pv_divergence': pv_divergence
    }


def calculate_mfi(highs, lows, closes, volumes, period=14):
    typical_prices = (highs + lows + closes) / 3
    money_flows = typical_prices * volumes
    
    positive_flow = 0
    negative_flow = 0
    
    for i in range(len(typical_prices) - period, len(typical_prices)):
        if i > 0 and typical_prices[i] > typical_prices[i-1]:
            positive_flow += money_flows[i]
        elif i > 0:
            negative_flow += money_flows[i]
    
    if negative_flow == 0:
        return 100
    
    mfr = positive_flow / negative_flow
    mfi = 100 - (100 / (1 + mfr))
    return mfi


def calculate_obv(closes, volumes):
    obv = [0]
    for i in range(1, len(closes)):
        if closes[i] > closes[i-1]:
            obv.append(obv[-1] + volumes[i])
        elif closes[i] < closes[i-1]:
            obv.append(obv[-1] - volumes[i])
        else:
            obv.append(obv[-1])
    return np.array(obv)


# ============================================
# SCORING
# ============================================

def calculate_fundamental_score(row):
    score = 0
    
    pe = row['PE']
    if pe != 'N/A':
        if pe < 15: score += 20
        elif pe < 25: score += 15
        elif pe < 35: score += 10
    
    pb = row['PB']
    if pb != 'N/A':
        if pb < 2: score += 15
        elif pb < 3: score += 10
        elif pb < 5: score += 5
    
    roe = row['ROE']
    if roe != 'N/A':
        if roe > 20: score += 25
        elif roe > 15: score += 20
        elif roe > 10: score += 15
        elif roe > 5: score += 10
    
    margin = row['Net_Margin']
    if margin != 'N/A':
        if margin > 15: score += 20
        elif margin > 10: score += 15
        elif margin > 5: score += 10
        elif margin > 0: score += 5
    
    der = row['DER']
    if der != 'N/A':
        if der < 0.5: score += 20
        elif der < 1: score += 15
        elif der < 1.5: score += 10
        elif der < 2: score += 5
    
    return min(score, 100)


def calculate_technical_score(row):
    score = 0
    
    if row['Above_SMA20']: score += 15
    if row['Above_SMA50']: score += 15
    
    rsi = row['RSI']
    if 40 <= rsi <= 70:
        score += 25
    elif 30 <= rsi < 40:
        score += 20
    elif 70 < rsi <= 80:
        score += 15
    elif rsi < 30:
        score += 10
    
    vol_ratio = row['Vol_Ratio']
    if vol_ratio > 1.5:
        score += 25
    elif vol_ratio > 1.2:
        score += 20
    elif vol_ratio > 1:
        score += 15
    else:
        score += 10
    
    pct_from_high = row['Pct_From_High']
    if pct_from_high > -10:
        score += 20
    elif pct_from_high > -20:
        score += 15
    elif pct_from_high > -30:
        score += 10
    elif pct_from_high > -50:
        score += 5
    
    return min(score, 100)


def calculate_bandarmology_score(row):
    score = 0
    
    accum = row['Accum_Signal']
    if accum == 'STRONG_ACCUM':
        score += 40
    elif accum == 'ACCUM':
        score += 30
    elif accum == 'NEUTRAL':
        score += 20
    
    mfi = row['MFI']
    if 40 <= mfi <= 60:
        score += 30
    elif (30 <= mfi < 40) or (60 < mfi <= 70):
        score += 25
    elif mfi < 30:
        score += 20
    else:
        score += 10
    
    if row['OBV_Trend'] == 'UP':
        score += 20
    else:
        score += 10
    
    if row['PV_Divergence']:
        score += 10
    
    return min(score, 100)


def generate_signal(score):
    if score >= 75:
        return 'STRONG BUY'
    elif score >= 65:
        return 'BUY'
    elif score >= 50:
        return 'HOLD'
    elif score >= 35:
        return 'WEAK'
    else:
        return 'AVOID'


def check_red_flags(row):
    flags = []
    
    if row['DER'] != 'N/A' and row['DER'] > 2:
        flags.append('HIGH_DEBT')
    if row['Net_Margin'] != 'N/A' and row['Net_Margin'] < 0:
        flags.append('NEG_MARGIN')
    if row['Accum_Signal'] == 'DISTRIB' and row['OBV_Trend'] == 'DOWN':
        flags.append('DISTRIBUTION')
    if row['RSI'] > 80:
        flags.append('OVERBOUGHT')
    
    return '|'.join(flags) if flags else 'CLEAR'


def generate_entry_timing(row, score):
    if (score >= 65 and 
        row['RSI'] < 70 and 
        row['Accum_Signal'] in ['ACCUM', 'STRONG_ACCUM'] and 
        row['Above_SMA20']):
        return 'ENTRY NOW'
    elif score >= 65 and not row['Above_SMA20']:
        return 'WAIT PULLBACK'
    else:
        return 'NOT READY'


# ============================================
# GOOGLE SHEETS
# ============================================

def write_to_google_sheets(df):
    try:
        creds_json = os.environ.get('GOOGLE_CREDENTIALS')
        if not creds_json:
            raise ValueError("GOOGLE_CREDENTIALS not found")
        
        creds_dict = json.loads(creds_json)
        creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
        
        service = build('sheets', 'v4', credentials=creds)
        
        values = [df.columns.tolist()] + df.values.tolist()
        body = {'values': values}
        
        service.spreadsheets().values().clear(
            spreadsheetId=SPREADSHEET_ID,
            range='RAW_DATA!A:Z'
        ).execute()
        
        result = service.spreadsheets().values().update(
            spreadsheetId=SPREADSHEET_ID,
            range='RAW_DATA!A1',
            valueInputOption='RAW',
            body=body
        ).execute()
        
        print(f"✅ Written {result.get('updatedCells')} cells to Google Sheets")
        
        write_scoring_sheet(service, df)
        
    except Exception as e:
        print(f"❌ Error writing to Google Sheets: {e}")
        raise


def write_scoring_sheet(service, df):
    scoring_df = df[['Ticker', 'Price', 'Total_Score', 'Fund_Score', 
                     'Tech_Score', 'Bandar_Score', 'Signal', 
                     'Entry_Timing', 'Red_Flags']].copy()
    
    scoring_df = scoring_df.sort_values('Total_Score', ascending=False)
    
    values = [scoring_df.columns.tolist()] + scoring_df.values.tolist()
    body = {'values': values}
    
    service.spreadsheets().values().clear(
        spreadsheetId=SPREADSHEET_ID,
        range='SCORING!A:I'
    ).execute()
    
    service.spreadsheets().values().update(
        spreadsheetId=SPREADSHEET_ID,
        range='SCORING!A1',
        valueInputOption='RAW',
        body=body
    ).execute()
    
    print("✅ Scoring data written to SCORING sheet")


# ============================================
# MAIN
# ============================================

def main():
    print("="*80)
    print("🚀 STOCK SCREENER - AUTOMATED RUN")
    print("="*80)
    print(f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Tickers to process: {len(TICKERS)}")
    print("="*80)
    
    results = []
    for ticker in TICKERS:
        data = get_comprehensive_data(ticker)
        if data:
            results.append(data)
    
    if not results:
        print("❌ No data collected. Exiting.")
        return
    
    df = pd.DataFrame(results)
    
    print("\n" + "="*80)
    print("📊 CALCULATING SCORES")
    print("="*80)
    
    df['Fund_Score'] = df.apply(calculate_fundamental_score, axis=1)
    df['Tech_Score'] = df.apply(calculate_technical_score, axis=1)
    df['Bandar_Score'] = df.apply(calculate_bandarmology_score, axis=1)
    
    df['Total_Score'] = (df['Fund_Score'] * 0.3 + 
                         df['Tech_Score'] * 0.3 + 
                         df['Bandar_Score'] * 0.4).round(2)
    
    df['Signal'] = df['Total_Score'].apply(generate_signal)
    df['Red_Flags'] = df.apply(check_red_flags, axis=1)
    df['Entry_Timing'] = df.apply(lambda row: generate_entry_timing(row, row['Total_Score']), axis=1)
    
    df = df.sort_values('Total_Score', ascending=False)
    
    print("\n🎯 TOP 10 OPPORTUNITIES:")
    print(df[['Ticker', 'Price', 'Total_Score', 'Signal', 'Entry_Timing']].head(10).to_string(index=False))
    
    print("\n" + "="*80)
    print("📤 UPLOADING TO GOOGLE SHEETS")
    print("="*80)
    
    write_to_google_sheets(df)
    
    print("\n" + "="*80)
    print("✅ SCREENER COMPLETED SUCCESSFULLY!")
    print("="*80)


if __name__ == '__main__':
    main()
