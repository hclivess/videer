"""
File utilities for videer
Handles file operations, timestamps, and metadata
"""

import os
import shutil
import datetime
import platform
import subprocess
import logging
from typing import Optional, Dict, Any, List, Tuple


class FileOperations:
    """Handles file operations and metadata preservation"""
    
    def preserve_timestamps(self, source_file: str, dest_file: str, 
                           logger: Optional[logging.Logger] = None) -> bool:
        """
        Preserve timestamps from source file to destination file
        Returns True if successful
        """
        try:
            # Get original file timestamps
            orig_stat = os.stat(source_file)
            orig_atime = orig_stat.st_atime
            orig_mtime = orig_stat.st_mtime
            orig_ctime = orig_stat.st_ctime
            
            # Create datetime objects for timestamps
            creation_time = datetime.datetime.fromtimestamp(orig_ctime)
            access_time = datetime.datetime.fromtimestamp(orig_atime)
            mod_time = datetime.datetime.fromtimestamp(orig_mtime)
            
            # Restore access and modification times
            os.utime(dest_file, (orig_atime, orig_mtime))
            
            # Handle creation time for different platforms
            system = platform.system()
            
            if system == 'Windows':
                # Use PowerShell to set creation time on Windows
                powershell_cmd = f'(Get-Item "{dest_file}").CreationTime = (Get-Date "{creation_time}")'
                result = subprocess.run(
                    ['powershell', '-Command', powershell_cmd],
                    capture_output=True,
                    text=True
                )
                
                if result.returncode != 0 and logger:
                    logger.warning(f"Failed to set creation time on Windows: {result.stderr}")
                    
            elif system == 'Darwin':  # macOS
                # Use SetFile for macOS (requires developer tools)
                try:
                    subprocess.run(
                        ['SetFile', '-d', creation_time.strftime("%m/%d/%Y %H:%M:%S"), dest_file],
                        check=True,
                        capture_output=True
                    )
                except (subprocess.CalledProcessError, FileNotFoundError) as e:
                    if logger:
                        logger.warning(f"Failed to set creation time on macOS: {e}")
            
            # For Linux, creation time (birth time) is filesystem-dependent and often not modifiable
            
            if logger:
                logger.info(f"Preserved file timestamps: "
                           f"Access={access_time}, "
                           f"Modified={mod_time}, "
                           f"Creation={creation_time}")
            
            return True
            
        except Exception as e:
            if logger:
                logger.warning(f"Error preserving timestamps: {e}")
            return False
    
    def replace_file(self, new_file: str, original_file: str,
                    logger: Optional[logging.Logger] = None) -> bool:
        """
        Replace original file with new file, preserving original as .old
        Returns True if successful
        """
        try:
            if not os.path.exists(new_file):
                if logger:
                    logger.error(f"New file does not exist: {new_file}")
                return False
            
            # Get original file timestamps before replacement
            orig_stat = os.stat(original_file)
            orig_atime = orig_stat.st_atime
            orig_mtime = orig_stat.st_mtime
            
            # Create backup filename
            orig_extension = os.path.splitext(original_file)[1]
            old_file_name = f"{original_file}.old{orig_extension}"
            
            # If old file already exists, remove it
            if os.path.exists(old_file_name):
                os.remove(old_file_name)
            
            # Rename original to .old
            os.rename(original_file, old_file_name)
            
            # Move new file to original location
            shutil.move(new_file, original_file)
            
            # Restore original timestamps
            os.utime(original_file, (orig_atime, orig_mtime))
            
            if logger:
                logger.info(f"Replaced {original_file} with {new_file}")
                logger.info(f"Original backed up to {old_file_name}")
                logger.info(f"Preserved original timestamps")
            
            return True
            
        except Exception as e:
            if logger:
                logger.error(f"Error replacing file: {e}")
            return False
    
    def safe_delete(self, filepath: str, 
                   logger: Optional[logging.Logger] = None) -> bool:
        """
        Safely delete a file
        Returns True if successful
        """
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
                if logger:
                    logger.info(f"Deleted file: {filepath}")
                return True
            return False
            
        except Exception as e:
            if logger:
                logger.error(f"Error deleting file {filepath}: {e}")
            return False
    
    def create_backup(self, filepath: str, backup_suffix: str = ".backup") -> Optional[str]:
        """
        Create a backup copy of a file
        Returns backup filepath if successful
        """
        try:
            backup_path = filepath + backup_suffix
            
            # If backup already exists, add number
            if os.path.exists(backup_path):
                counter = 1
                while os.path.exists(f"{filepath}.backup{counter}"):
                    counter += 1
                backup_path = f"{filepath}.backup{counter}"
            
            shutil.copy2(filepath, backup_path)
            return backup_path
            
        except Exception as e:
            return None
    
    def get_file_info(self, filepath: str) -> Dict[str, Any]:
        """
        Get detailed file information
        Returns dictionary with file metadata
        """
        info = {}
        
        try:
            if not os.path.exists(filepath):
                return info
            
            stat = os.stat(filepath)
            
            info['path'] = filepath
            info['name'] = os.path.basename(filepath)
            info['directory'] = os.path.dirname(filepath)
            info['extension'] = os.path.splitext(filepath)[1]
            info['size_bytes'] = stat.st_size
            info['size_mb'] = stat.st_size / (1024 * 1024)
            info['size_gb'] = stat.st_size / (1024 * 1024 * 1024)
            
            # Timestamps
            info['created'] = datetime.datetime.fromtimestamp(stat.st_ctime)
            info['modified'] = datetime.datetime.fromtimestamp(stat.st_mtime)
            info['accessed'] = datetime.datetime.fromtimestamp(stat.st_atime)
            
            # Permissions
            info['readable'] = os.access(filepath, os.R_OK)
            info['writable'] = os.access(filepath, os.W_OK)
            info['executable'] = os.access(filepath, os.X_OK)
            
        except Exception as e:
            info['error'] = str(e)
        
        return info
    
    def verify_disk_space(self, output_dir: str, required_mb: float) -> bool:
        """
        Check if there's enough disk space
        Returns True if sufficient space available
        """
        try:
            stat = shutil.disk_usage(output_dir)
            available_mb = stat.free / (1024 * 1024)
            return available_mb >= required_mb
            
        except Exception:
            return False
    
    def create_directory_structure(self, base_path: str, 
                                  subdirs: List[str]) -> bool:
        """
        Create directory structure
        Returns True if successful
        """
        try:
            for subdir in subdirs:
                dir_path = os.path.join(base_path, subdir)
                os.makedirs(dir_path, exist_ok=True)
            return True
            
        except Exception:
            return False


class MediaInfo:
    """Extract media file information using FFprobe"""
    
    def __init__(self):
        self.ffprobe_path = self._find_ffprobe()
    
    def _find_ffprobe(self) -> Optional[str]:
        """Find FFprobe executable"""
        import shutil
        
        # Check system PATH
        ffprobe = shutil.which('ffprobe')
        if ffprobe:
            return ffprobe
        
        # Check current directory
        current_dir = os.path.dirname(os.path.abspath(__file__))
        
        # Windows
        ffprobe_local = os.path.join(current_dir, 'ffprobe.exe')
        if os.path.exists(ffprobe_local):
            return ffprobe_local
        
        # Unix
        ffprobe_local = os.path.join(current_dir, 'ffprobe')
        if os.path.exists(ffprobe_local):
            return ffprobe_local
        
        return None
    
    def get_video_info(self, filepath: str) -> Dict[str, Any]:
        """
        Get video file information using FFprobe
        Returns dictionary with video metadata
        """
        if not self.ffprobe_path:
            return {"error": "FFprobe not found"}
        
        try:
            # Build FFprobe command
            cmd = [
                self.ffprobe_path,
                '-v', 'quiet',
                '-print_format', 'json',
                '-show_format',
                '-show_streams',
                filepath
            ]
            
            # Execute FFprobe
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30
            )
            
            if result.returncode != 0:
                return {"error": f"FFprobe failed: {result.stderr}"}
            
            # Parse JSON output
            import json
            data = json.loads(result.stdout)
            
            # Extract relevant information
            info = {
                "format": data.get("format", {}).get("format_name"),
                "duration": float(data.get("format", {}).get("duration", 0)),
                "size": int(data.get("format", {}).get("size", 0)),
                "bitrate": int(data.get("format", {}).get("bit_rate", 0)),
                "streams": []
            }
            
            # Process streams
            for stream in data.get("streams", []):
                stream_info = {
                    "type": stream.get("codec_type"),
                    "codec": stream.get("codec_name"),
                    "profile": stream.get("profile")
                }
                
                if stream.get("codec_type") == "video":
                    stream_info.update({
                        "width": stream.get("width"),
                        "height": stream.get("height"),
                        "fps": self._parse_fps(stream.get("r_frame_rate")),
                        "pixel_format": stream.get("pix_fmt"),
                        "sample_aspect_ratio": stream.get("sample_aspect_ratio"),
                        "display_aspect_ratio": stream.get("display_aspect_ratio")
                    })
                
                elif stream.get("codec_type") == "audio":
                    stream_info.update({
                        "channels": stream.get("channels"),
                        "channel_layout": stream.get("channel_layout"),
                        "sample_rate": stream.get("sample_rate"),
                        "bit_rate": stream.get("bit_rate")
                    })
                
                info["streams"].append(stream_info)
            
            # Extract PAR/DAR from first video stream
            for stream in info["streams"]:
                if stream["type"] == "video":
                    info["par"] = stream.get("sample_aspect_ratio", "1:1")
                    info["dar"] = stream.get("display_aspect_ratio", "16:9")
                    info["width"] = stream.get("width")
                    info["height"] = stream.get("height")
                    info["fps"] = stream.get("fps")
                    break
            
            return info
            
        except subprocess.TimeoutExpired:
            return {"error": "FFprobe timeout"}
        except Exception as e:
            return {"error": str(e)}
    
    def _parse_fps(self, fps_string: str) -> float:
        """Parse FPS from fraction string"""
        if not fps_string:
            return 0.0
        
        try:
            if '/' in fps_string:
                num, den = fps_string.split('/')
                return float(num) / float(den)
            return float(fps_string)
        except:
            return 0.0
    
    def get_par_dar(self, filepath: str) -> Tuple[str, str]:
        """
        Get PAR and DAR for a video file
        Returns (PAR, DAR) tuple
        """
        info = self.get_video_info(filepath)
        
        par = info.get("par", "1:1")
        dar = info.get("dar", "16:9")
        
        # Validate and clean up ratios
        par = self._clean_ratio(par)
        dar = self._clean_ratio(dar)
        
        return par, dar
    
    def _clean_ratio(self, ratio: str) -> str:
        """Clean up aspect ratio string"""
        if not ratio or ratio == "N/A" or ratio == "0:0":
            return "1:1"
        
        # Handle decimal ratios
        if '.' in ratio and ':' not in ratio:
            try:
                value = float(ratio)
                # Convert to fraction
                from fractions import Fraction
                frac = Fraction(value).limit_denominator(1000)
                return f"{frac.numerator}:{frac.denominator}"
            except:
                return "1:1"
        
        return ratio
    
    def calculate_output_dimensions(self, width: int, height: int,
                                   par: str, dar: str) -> Tuple[int, int]:
        """
        Calculate output dimensions based on PAR/DAR
        Returns (width, height) tuple
        """
        try:
            # Parse PAR
            if ':' in par:
                par_num, par_den = map(int, par.split(':'))
                par_value = par_num / par_den
            else:
                par_value = 1.0
            
            # Parse DAR
            if ':' in dar:
                dar_num, dar_den = map(int, dar.split(':'))
                dar_value = dar_num / dar_den
            else:
                dar_value = 16 / 9
            
            # Calculate corrected dimensions
            if par_value != 1.0:
                # Apply PAR correction
                corrected_width = int(width * par_value)
                return corrected_width, height
            elif dar_value != (width / height):
                # Apply DAR correction
                corrected_width = int(height * dar_value)
                return corrected_width, height
            
            return width, height
            
        except:
            return width, height