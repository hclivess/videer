"""
PAR/DAR Utilities for videer
Handles pixel and display aspect ratio calculations
"""

from typing import Tuple, Optional, Dict, Any
from fractions import Fraction
import math


class AspectRatioCalculator:
    """Calculate and convert between different aspect ratio formats"""
    
    @staticmethod
    def parse_ratio(ratio_str: str) -> Optional[Tuple[int, int]]:
        """
        Parse aspect ratio string to tuple
        Accepts formats: "16:9", "16/9", "1.778", etc.
        Returns (numerator, denominator) or None if invalid
        """
        if not ratio_str:
            return None
        
        # Clean up the string
        ratio_str = ratio_str.strip()
        
        try:
            # Handle colon format (16:9)
            if ':' in ratio_str:
                parts = ratio_str.split(':')
                if len(parts) == 2:
                    return (int(parts[0]), int(parts[1]))
            
            # Handle slash format (16/9)
            elif '/' in ratio_str:
                parts = ratio_str.split('/')
                if len(parts) == 2:
                    return (int(parts[0]), int(parts[1]))
            
            # Handle decimal format (1.778)
            else:
                value = float(ratio_str)
                # Convert to fraction with reasonable denominator limit
                frac = Fraction(value).limit_denominator(1000)
                return (frac.numerator, frac.denominator)
        
        except (ValueError, ZeroDivisionError):
            pass
        
        return None
    
    @staticmethod
    def ratio_to_string(numerator: int, denominator: int) -> str:
        """Convert ratio to standard string format"""
        # Simplify the fraction
        gcd = math.gcd(numerator, denominator)
        num = numerator // gcd
        den = denominator // gcd
        return f"{num}:{den}"
    
    @staticmethod
    def ratio_to_decimal(numerator: int, denominator: int) -> float:
        """Convert ratio to decimal value"""
        if denominator == 0:
            return 0.0
        return numerator / denominator
    
    @staticmethod
    def simplify_ratio(numerator: int, denominator: int) -> Tuple[int, int]:
        """Simplify ratio to lowest terms"""
        gcd = math.gcd(numerator, denominator)
        return (numerator // gcd, denominator // gcd)
    
    @staticmethod
    def calculate_dimensions(width: int, height: int, 
                           par: str = "1:1") -> Tuple[int, int]:
        """
        Calculate display dimensions based on PAR
        Returns (display_width, display_height)
        """
        par_tuple = AspectRatioCalculator.parse_ratio(par)
        if not par_tuple:
            return (width, height)
        
        par_num, par_den = par_tuple
        if par_den == 0:
            return (width, height)
        
        par_value = par_num / par_den
        
        # Calculate display width with PAR applied
        display_width = int(width * par_value)
        display_height = height
        
        return (display_width, display_height)
    
    @staticmethod
    def calculate_sar_from_dar_par(dar: str, par: str, 
                                   width: int, height: int) -> str:
        """
        Calculate Storage Aspect Ratio from DAR and PAR
        SAR = DAR / PAR
        """
        dar_tuple = AspectRatioCalculator.parse_ratio(dar)
        par_tuple = AspectRatioCalculator.parse_ratio(par)
        
        if not dar_tuple or not par_tuple:
            return f"{width}:{height}"
        
        dar_value = dar_tuple[0] / dar_tuple[1]
        par_value = par_tuple[0] / par_tuple[1]
        
        if par_value == 0:
            return f"{width}:{height}"
        
        sar_value = dar_value / par_value
        
        # Convert to fraction
        frac = Fraction(sar_value).limit_denominator(1000)
        return AspectRatioCalculator.ratio_to_string(frac.numerator, frac.denominator)


class PARHandler:
    """Handle PAR-specific operations"""
    
    # Common PAR values for different standards
    PAR_STANDARDS = {
        # NTSC
        "NTSC_4:3_704": "10:11",
        "NTSC_4:3_720": "10:11",
        "NTSC_16:9_704": "40:33",
        "NTSC_16:9_720": "40:33",
        
        # PAL
        "PAL_4:3_704": "12:11",
        "PAL_4:3_720": "12:11",
        "PAL_16:9_704": "16:11",
        "PAL_16:9_720": "16:11",
        
        # HDV
        "HDV_1080": "4:3",
        "HDV_720": "1:1",
        
        # DVCPRO HD
        "DVCPRO_HD_720": "3:2",
        "DVCPRO_HD_1080_50": "3:2",
        "DVCPRO_HD_1080_60": "3:2",
        
        # Modern
        "SQUARE": "1:1"
    }
    
    @staticmethod
    def detect_par_from_dimensions(width: int, height: int, 
                                   standard: str = "auto") -> str:
        """
        Detect likely PAR based on video dimensions and standard
        Returns PAR string
        """
        # Modern HD/4K formats typically use square pixels
        if width >= 1920 or height >= 1080:
            return "1:1"
        
        # Check for common SD dimensions
        if width == 720:
            if height == 480:  # NTSC
                return "10:11"  # Assuming 4:3, use 40:33 for 16:9
            elif height == 576:  # PAL
                return "12:11"  # Assuming 4:3, use 16:11 for 16:9
        
        # DV formats
        if width == 720 and height == 480:
            return "10:11"
        elif width == 720 and height == 576:
            return "12:11"
        
        # Default to square pixels
        return "1:1"
    
    @staticmethod
    def needs_resampling(par: str) -> bool:
        """Check if PAR needs resampling for square pixels"""
        if not par or par == "1:1" or par == "1/1":
            return False
        
        par_tuple = AspectRatioCalculator.parse_ratio(par)
        if not par_tuple:
            return False
        
        # Check if ratio equals 1
        return par_tuple[0] != par_tuple[1]
    
    @staticmethod
    def calculate_resampled_dimensions(width: int, height: int, 
                                      par: str, 
                                      preserve_height: bool = True) -> Tuple[int, int]:
        """
        Calculate dimensions after resampling to square pixels
        
        Args:
            width: Original width
            height: Original height
            par: Pixel aspect ratio
            preserve_height: If True, keep height and adjust width
                           If False, keep width and adjust height
        
        Returns:
            (new_width, new_height) for square pixels
        """
        par_tuple = AspectRatioCalculator.parse_ratio(par)
        if not par_tuple or par_tuple[1] == 0:
            return (width, height)
        
        par_value = par_tuple[0] / par_tuple[1]
        
        if preserve_height:
            # Adjust width to get square pixels
            new_width = int(width * par_value)
            # Round to even number for codec compatibility
            new_width = (new_width // 2) * 2
            return (new_width, height)
        else:
            # Adjust height to get square pixels
            new_height = int(height / par_value)
            # Round to even number for codec compatibility
            new_height = (new_height // 2) * 2
            return (width, new_height)


class DARHandler:
    """Handle DAR-specific operations"""
    
    # Common DAR values
    DAR_STANDARDS = {
        "4:3": (4, 3),
        "16:9": (16, 9),
        "16:10": (16, 10),
        "21:9": (21, 9),
        "1:1": (1, 1),
        "2.35:1": (235, 100),
        "2.39:1": (239, 100),
        "2.40:1": (240, 100),
        "1.85:1": (185, 100)
    }
    
    @staticmethod
    def detect_dar_from_dimensions(width: int, height: int) -> str:
        """
        Detect DAR from video dimensions
        Returns closest standard DAR
        """
        if height == 0:
            return "16:9"
        
        ratio = width / height
        
        # Find closest standard DAR
        closest_dar = "16:9"
        min_diff = float('inf')
        
        for dar_name, (num, den) in DARHandler.DAR_STANDARDS.items():
            dar_value = num / den
            diff = abs(ratio - dar_value)
            
            if diff < min_diff:
                min_diff = diff
                closest_dar = dar_name
        
        # If very close to actual ratio, return simplified version
        if min_diff > 0.05:
            # Not close to any standard, return actual ratio
            frac = Fraction(ratio).limit_denominator(100)
            return f"{frac.numerator}:{frac.denominator}"
        
        return closest_dar
    
    @staticmethod
    def calculate_dimensions_for_dar(current_width: int, current_height: int,
                                    target_dar: str, 
                                    preserve: str = "height") -> Tuple[int, int]:
        """
        Calculate new dimensions to achieve target DAR
        
        Args:
            current_width: Current width
            current_height: Current height
            target_dar: Target display aspect ratio
            preserve: "height" to keep height and adjust width
                     "width" to keep width and adjust height
        
        Returns:
            (new_width, new_height) for target DAR
        """
        dar_tuple = AspectRatioCalculator.parse_ratio(target_dar)
        if not dar_tuple or dar_tuple[1] == 0:
            return (current_width, current_height)
        
        dar_value = dar_tuple[0] / dar_tuple[1]
        
        if preserve == "height":
            new_width = int(current_height * dar_value)
            # Round to even number for codec compatibility
            new_width = (new_width // 2) * 2
            return (new_width, current_height)
        else:  # preserve width
            new_height = int(current_width / dar_value)
            # Round to even number for codec compatibility
            new_height = (new_height // 2) * 2
            return (current_width, new_height)
    
    @staticmethod
    def add_letterbox_or_pillarbox(width: int, height: int, 
                                  target_dar: str) -> Dict[str, Any]:
        """
        Calculate padding needed for letterbox/pillarbox
        
        Returns dict with:
            - type: "letterbox", "pillarbox", or "none"
            - top: top padding
            - bottom: bottom padding
            - left: left padding
            - right: right padding
            - final_width: final width after padding
            - final_height: final height after padding
        """
        dar_tuple = AspectRatioCalculator.parse_ratio(target_dar)
        if not dar_tuple or dar_tuple[1] == 0:
            return {"type": "none"}
        
        target_ratio = dar_tuple[0] / dar_tuple[1]
        current_ratio = width / height if height > 0 else 1
        
        result = {
            "type": "none",
            "top": 0,
            "bottom": 0,
            "left": 0,
            "right": 0,
            "final_width": width,
            "final_height": height
        }
        
        if abs(current_ratio - target_ratio) < 0.01:
            # Already correct ratio
            return result
        
        if current_ratio > target_ratio:
            # Video is wider than target - add letterbox (top/bottom bars)
            new_height = int(width / target_ratio)
            padding = new_height - height
            
            result["type"] = "letterbox"
            result["top"] = padding // 2
            result["bottom"] = padding - (padding // 2)
            result["final_height"] = new_height
            
        else:
            # Video is taller than target - add pillarbox (side bars)
            new_width = int(height * target_ratio)
            padding = new_width - width
            
            result["type"] = "pillarbox"
            result["left"] = padding // 2
            result["right"] = padding - (padding // 2)
            result["final_width"] = new_width
        
        # Ensure even dimensions
        result["final_width"] = (result["final_width"] // 2) * 2
        result["final_height"] = (result["final_height"] // 2) * 2
        
        return result


class AspectRatioFFmpegFilter:
    """Generate FFmpeg filter strings for aspect ratio operations"""
    
    @staticmethod
    def create_resample_filter(width: int, height: int, par: str) -> str:
        """Create FFmpeg filter to resample non-square pixels"""
        if not PARHandler.needs_resampling(par):
            return ""
        
        new_width, new_height = PARHandler.calculate_resampled_dimensions(
            width, height, par, preserve_height=True
        )
        
        # Use high-quality Lanczos scaling
        return f"scale={new_width}:{new_height}:flags=lanczos,setsar=1:1"
    
    @staticmethod
    def create_dar_filter(width: int, height: int, target_dar: str,
                         method: str = "scale") -> str:
        """
        Create FFmpeg filter for DAR adjustment
        
        Args:
            method: "scale" to resize, "pad" to add black bars
        """
        if method == "scale":
            new_width, new_height = DARHandler.calculate_dimensions_for_dar(
                width, height, target_dar, preserve="height"
            )
            return f"scale={new_width}:{new_height}:flags=lanczos,setdar={target_dar}"
        
        elif method == "pad":
            padding = DARHandler.add_letterbox_or_pillarbox(width, height, target_dar)
            if padding["type"] == "letterbox":
                return f"pad={width}:{padding['final_height']}:0:{padding['top']}"
            elif padding["type"] == "pillarbox":
                return f"pad={padding['final_width']}:{height}:{padding['left']}:0"
        
        return ""
    
    @staticmethod
    def create_complete_filter_chain(width: int, height: int,
                                    par: str = "1:1",
                                    target_dar: Optional[str] = None,
                                    resample_par: bool = False) -> str:
        """
        Create complete FFmpeg filter chain for aspect ratio corrections
        """
        filters = []
        
        # Handle PAR resampling if needed
        if resample_par and PARHandler.needs_resampling(par):
            filter_str = AspectRatioFFmpegFilter.create_resample_filter(
                width, height, par
            )
            if filter_str:
                filters.append(filter_str)
                # Update dimensions after resampling
                width, height = PARHandler.calculate_resampled_dimensions(
                    width, height, par, preserve_height=True
                )
        
        # Handle DAR adjustment if specified
        if target_dar:
            filter_str = AspectRatioFFmpegFilter.create_dar_filter(
                width, height, target_dar, method="scale"
            )
            if filter_str:
                filters.append(filter_str)
        
        return ",".join(filters) if filters else ""