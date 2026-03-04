from datetime import date, timedelta
import numpy as np

class OptionSymbolBuilder:
    @staticmethod
    def _round_to_nearest_strike(price: float, spacing: float) -> float:
        """Round price to nearest valid strike price based on strike spacing"""
        return round(price / spacing) * spacing

    @staticmethod
    def _is_third_friday(d: date) -> bool:
        """Check if date is the third Friday of its month"""
        # Find first day of the month
        first = date(d.year, d.month, 1)
        # Find first Friday
        friday = first + timedelta(days=((4 - first.weekday()) % 7))
        # Find third Friday
        third_friday = friday + timedelta(days=14)  # Fixed: removed parentheses around 14
        #print(f"Third Friday: {third_friday}")
        return d == third_friday

    @staticmethod
    def build_symbols(base_symbol: str, expiry: date, current_price: float, strike_range: int, strike_spacing: float) -> list:
        """
        Builds a list of option symbols for both calls and puts
        Returns: List of option symbols in ThinkorSwim format
        Example: .SPY250129C601
        """

        # Only convert symbols if it's NOT the third Friday of the month
        # I need to figure out how to display SPX afternoon expiry contract on 3rd friday
        if not OptionSymbolBuilder._is_third_friday(expiry):
            if base_symbol == "SPX":
                base_symbol = "SPXW"
            elif base_symbol == "NDX":
                base_symbol = "NDXP"
            elif base_symbol == "RUT":
                base_symbol = "RUTW"
  
        # Round current price to nearest valid strike
        rounded_price = OptionSymbolBuilder._round_to_nearest_strike(current_price, strike_spacing)
        
        # Generate strike prices using numpy arange
        num_strikes = int(2 * strike_range / strike_spacing) + 1
        #print(f"Num Strikes: {num_strikes}")
        strikes = np.linspace(
            rounded_price - strike_range,
            rounded_price + strike_range,
            num_strikes
        )
        
        symbols = []
        date_str = expiry.strftime("%y%m%d")
        
        for strike in strikes:
            # Format strike string: only show decimal for .5 strikes
            if (strike_spacing in [0.5, 2.5] and 
                abs(strike % 1 - 0.5) < 0.001):  # Handle floating point comparison
                strike_str = f"{strike:.1f}"
            else:
                strike_str = f"{int(strike)}"
                
            call_symbol = f".{base_symbol}{date_str}C{strike_str}"
            put_symbol = f".{base_symbol}{date_str}P{strike_str}"
            symbols.extend([call_symbol, put_symbol])
        
        return symbols
