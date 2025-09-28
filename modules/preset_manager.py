"""
Preset Manager for videer
Handles saving and loading presets
"""

import os
import json
from typing import Dict, Any, List, Optional
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QInputDialog, QMessageBox, QFileDialog

from config import QUALITY_PRESETS, PAR_PRESETS, DAR_PRESETS


class PresetManager(QObject):
    """Manages video processing presets"""
    
    def __init__(self, main_window):
        super().__init__()
        self.main_window = main_window
        self.presets_dir = self._get_presets_dir()
        self._ensure_presets_dir()
    
    def _get_presets_dir(self) -> str:
        """Get presets directory path"""
        app_dir = os.path.dirname(os.path.abspath(__file__))
        return os.path.join(os.path.dirname(app_dir), "presets")
    
    def _ensure_presets_dir(self):
        """Ensure presets directory exists"""
        if not os.path.exists(self.presets_dir):
            os.makedirs(self.presets_dir)
    
    def save_preset(self):
        """Save current settings as preset"""
        # Get preset name from user
        name, ok = QInputDialog.getText(
            self.main_window,
            "Save Preset",
            "Enter preset name:"
        )
        
        if not ok or not name:
            return
        
        # Get current settings
        settings = self.main_window.ui_manager.get_current_settings()
        
        # Add metadata
        preset_data = {
            "name": name,
            "version": "2.1",
            "settings": settings
        }
        
        # Save to file
        preset_file = os.path.join(self.presets_dir, f"{self._sanitize_filename(name)}.json")
        
        try:
            with open(preset_file, 'w') as f:
                json.dump(preset_data, f, indent=4)
            
            QMessageBox.information(
                self.main_window,
                "Preset Saved",
                f"Preset '{name}' saved successfully!"
            )
            
        except Exception as e:
            QMessageBox.critical(
                self.main_window,
                "Save Failed",
                f"Failed to save preset: {str(e)}"
            )
    
    def load_preset(self):
        """Load a saved preset"""
        # Get list of available presets
        presets = self.get_available_presets()
        
        if not presets:
            QMessageBox.warning(
                self.main_window,
                "No Presets",
                "No saved presets found."
            )
            return
        
        # Let user choose preset
        preset_names = [p['name'] for p in presets]
        name, ok = QInputDialog.getItem(
            self.main_window,
            "Load Preset",
            "Select preset:",
            preset_names,
            0,
            False
        )
        
        if not ok or not name:
            return
        
        # Find and load the preset
        for preset in presets:
            if preset['name'] == name:
                self._apply_preset_file(preset['file'])
                break
    
    def _apply_preset_file(self, preset_file: str):
        """Apply settings from preset file"""
        try:
            with open(preset_file, 'r') as f:
                preset_data = json.load(f)
            
            settings = preset_data.get('settings', {})
            self.apply_settings(settings)
            
            QMessageBox.information(
                self.main_window,
                "Preset Loaded",
                f"Preset '{preset_data.get('name')}' loaded successfully!"
            )
            
        except Exception as e:
            QMessageBox.critical(
                self.main_window,
                "Load Failed",
                f"Failed to load preset: {str(e)}"
            )
    
    def apply_settings(self, settings: Dict[str, Any]):
        """Apply settings to UI controls"""
        ui = self.main_window.ui_manager
        
        # Video codec
        if 'video_codec' in settings:
            for button in ui.codec_groups['video'].buttons():
                if button.property("value") == settings['video_codec']:
                    button.setChecked(True)
                    break
        
        # Audio codec
        if 'audio_codec' in settings:
            for button in ui.codec_groups['audio'].buttons():
                if button.property("value") == settings['audio_codec']:
                    button.setChecked(True)
                    break
        
        # Numeric values
        if 'crf' in settings:
            ui.controls['crf'].setValue(settings['crf'])
        if 'abr' in settings:
            ui.controls['abr'].setValue(settings['abr'])
        if 'threads' in settings:
            ui.controls['threads'].setValue(settings['threads'])
        
        # Combo boxes
        if 'preset' in settings:
            index = ui.controls['preset'].findText(settings['preset'])
            if index >= 0:
                ui.controls['preset'].setCurrentIndex(index)
        
        if 'output_format' in settings:
            index = ui.controls['output_format'].findText(settings['output_format'])
            if index >= 0:
                ui.controls['output_format'].setCurrentIndex(index)
        
        # PAR/DAR settings
        if 'par_mode' in settings:
            index = ui.controls['par_mode'].findText(settings['par_mode'])
            if index >= 0:
                ui.controls['par_mode'].setCurrentIndex(index)
        
        if 'par_custom' in settings:
            ui.controls['par_custom'].setText(settings['par_custom'])
        
        if 'dar_mode' in settings:
            index = ui.controls['dar_mode'].findText(settings['dar_mode'])
            if index >= 0:
                ui.controls['dar_mode'].setCurrentIndex(index)
        
        if 'dar_custom' in settings:
            ui.controls['dar_custom'].setText(settings['dar_custom'])
        
        # Checkboxes
        checkbox_fields = [
            'stereo', 'deinterlace', 'tff', 'reduce_fps',
            'use_avisynth', 'use_ffms2', 'transcode_video',
            'transcode_audio', 'corrupt_fix', 'replace_files'
        ]
        
        for field in checkbox_fields:
            if field in settings and field in ui.controls:
                ui.controls[field].setChecked(settings[field])
        
        # Text fields
        if 'ffmpeg_extras' in settings:
            ui.controls['ffmpeg_extras'].setText(settings['ffmpeg_extras'])
        
        if 'avisynth_extras' in settings:
            ui.controls['avisynth_extras'].setPlainText(settings['avisynth_extras'])
    
    def apply_preset(self, preset_name: str):
        """Apply a built-in quality preset"""
        if preset_name not in QUALITY_PRESETS:
            return
        
        preset = QUALITY_PRESETS[preset_name]
        settings = {
            'video_codec': preset.get('video_codec'),
            'audio_codec': preset.get('audio_codec'),
            'crf': preset.get('crf'),
            'abr': preset.get('abr'),
            'preset': preset.get('preset'),
            'output_format': preset.get('output_format')
        }
        
        self.apply_settings(settings)
    
    def get_available_presets(self) -> List[Dict[str, str]]:
        """Get list of available preset files"""
        presets = []
        
        if not os.path.exists(self.presets_dir):
            return presets
        
        for filename in os.listdir(self.presets_dir):
            if filename.endswith('.json'):
                filepath = os.path.join(self.presets_dir, filename)
                try:
                    with open(filepath, 'r') as f:
                        data = json.load(f)
                        presets.append({
                            'name': data.get('name', filename[:-5]),
                            'file': filepath
                        })
                except:
                    continue
        
        return sorted(presets, key=lambda x: x['name'])
    
    def export_preset(self):
        """Export preset to chosen location"""
        # First save current settings
        settings = self.main_window.ui_manager.get_current_settings()
        
        # Get export location
        filepath, _ = QFileDialog.getSaveFileName(
            self.main_window,
            "Export Preset",
            "",
            "JSON Files (*.json);;All Files (*.*)"
        )
        
        if not filepath:
            return
        
        # Get preset name
        name, ok = QInputDialog.getText(
            self.main_window,
            "Export Preset",
            "Enter preset name:"
        )
        
        if not ok or not name:
            return
        
        # Create preset data
        preset_data = {
            "name": name,
            "version": "2.1",
            "settings": settings
        }
        
        try:
            with open(filepath, 'w') as f:
                json.dump(preset_data, f, indent=4)
            
            QMessageBox.information(
                self.main_window,
                "Export Successful",
                f"Preset exported to {filepath}"
            )
            
        except Exception as e:
            QMessageBox.critical(
                self.main_window,
                "Export Failed",
                f"Failed to export preset: {str(e)}"
            )
    
    def import_preset(self):
        """Import preset from file"""
        filepath, _ = QFileDialog.getOpenFileName(
            self.main_window,
            "Import Preset",
            "",
            "JSON Files (*.json);;All Files (*.*)"
        )
        
        if not filepath:
            return
        
        try:
            with open(filepath, 'r') as f:
                preset_data = json.load(f)
            
            # Validate preset
            if 'settings' not in preset_data:
                raise ValueError("Invalid preset file format")
            
            # Save to presets directory
            name = preset_data.get('name', 'Imported Preset')
            local_file = os.path.join(
                self.presets_dir,
                f"{self._sanitize_filename(name)}.json"
            )
            
            # Check if exists
            if os.path.exists(local_file):
                reply = QMessageBox.question(
                    self.main_window,
                    "Preset Exists",
                    f"Preset '{name}' already exists. Overwrite?",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
                )
                
                if reply == QMessageBox.StandardButton.No:
                    return
            
            # Save locally
            with open(local_file, 'w') as f:
                json.dump(preset_data, f, indent=4)
            
            # Apply if user wants
            reply = QMessageBox.question(
                self.main_window,
                "Import Successful",
                f"Preset '{name}' imported successfully. Apply now?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            
            if reply == QMessageBox.StandardButton.Yes:
                self.apply_settings(preset_data['settings'])
            
        except Exception as e:
            QMessageBox.critical(
                self.main_window,
                "Import Failed",
                f"Failed to import preset: {str(e)}"
            )
    
    def delete_preset(self):
        """Delete a saved preset"""
        presets = self.get_available_presets()
        
        if not presets:
            QMessageBox.warning(
                self.main_window,
                "No Presets",
                "No saved presets found."
            )
            return
        
        preset_names = [p['name'] for p in presets]
        name, ok = QInputDialog.getItem(
            self.main_window,
            "Delete Preset",
            "Select preset to delete:",
            preset_names,
            0,
            False
        )
        
        if not ok or not name:
            return
        
        # Confirm deletion
        reply = QMessageBox.question(
            self.main_window,
            "Confirm Deletion",
            f"Are you sure you want to delete preset '{name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.No:
            return
        
        # Delete the file
        for preset in presets:
            if preset['name'] == name:
                try:
                    os.remove(preset['file'])
                    QMessageBox.information(
                        self.main_window,
                        "Preset Deleted",
                        f"Preset '{name}' deleted successfully."
                    )
                except Exception as e:
                    QMessageBox.critical(
                        self.main_window,
                        "Delete Failed",
                        f"Failed to delete preset: {str(e)}"
                    )
                break
    
    def _sanitize_filename(self, name: str) -> str:
        """Sanitize preset name for filename"""
        # Remove invalid characters
        invalid_chars = '<>:"/\\|?*'
        for char in invalid_chars:
            name = name.replace(char, '')
        
        # Replace spaces with underscores
        name = name.replace(' ', '_')
        
        # Limit length
        if len(name) > 50:
            name = name[:50]
        
        return name if name else "preset"