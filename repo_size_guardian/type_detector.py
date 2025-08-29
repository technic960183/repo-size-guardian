"""
Type detection utilities for determining if blobs are text or binary.

Provides functions to classify blobs using OS file command and fallback heuristics.
"""

import subprocess
import tempfile
import os
import string
from typing import Dict, Optional, Tuple


def detect_type_with_file_command(blob_sha: str) -> Optional[Dict[str, any]]:
    """
    Detect file type using OS file --mime command.
    
    Args:
        blob_sha: SHA hash of the blob
        
    Returns:
        Dictionary with detection results or None if command fails
        Keys: is_binary, mime, confidence
    """
    try:
        # Get blob content and write to temporary file
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            # Get blob content using git cat-file
            result = subprocess.run(
                ['git', 'cat-file', '-p', blob_sha],
                stdout=temp_file,
                stderr=subprocess.PIPE,
                check=True
            )
            
            temp_path = temp_file.name
        
        try:
            # Run file --mime -b on the temporary file
            file_result = subprocess.run(
                ['file', '--mime', '-b', temp_path],
                capture_output=True,
                text=True,
                check=True
            )
            
            mime_output = file_result.stdout.strip()
            
            # Parse MIME type
            mime_parts = mime_output.split(';')
            mime_type = mime_parts[0].strip() if mime_parts else mime_output
            
            # Determine if binary based on MIME type
            is_binary = not (
                mime_type.startswith('text/') or
                mime_type in [
                    'application/json',
                    'application/xml',
                    'application/javascript',
                    'application/x-empty',  # Empty files
                    'inode/x-empty'  # Empty files (alternative format)
                ]
            )
            
            return {
                'is_binary': is_binary,
                'mime': mime_type,
                'confidence': 'high'
            }
            
        finally:
            # Clean up temporary file
            try:
                os.unlink(temp_path)
            except OSError:
                pass
                
    except (subprocess.CalledProcessError, OSError):
        return None


def detect_type_with_content_heuristics(blob_sha: str) -> Dict[str, any]:
    """
    Detect file type using content heuristics as fallback.
    
    Args:
        blob_sha: SHA hash of the blob
        
    Returns:
        Dictionary with detection results
        Keys: is_binary, mime, confidence
    """
    try:
        # Get blob content
        result = subprocess.run(
            ['git', 'cat-file', '-p', blob_sha],
            capture_output=True,
            check=True
        )
        
        content = result.stdout
        
        # Check for null bytes (strong indicator of binary)
        if b'\x00' in content:
            return {
                'is_binary': True,
                'mime': None,
                'confidence': 'high'
            }
            
        # If empty, consider it text
        if not content:
            return {
                'is_binary': False,
                'mime': None,
                'confidence': 'medium'
            }
            
        # Try to decode as UTF-8 and check printable ratio
        try:
            # First check how much of the content can be decoded without errors
            try:
                text_content = content.decode('utf-8')
                decoding_success = True
            except UnicodeDecodeError:
                # If strict decoding fails, use ignore but consider it less text-like
                text_content = content.decode('utf-8', errors='ignore')
                decoding_success = False
            
            # Calculate ratio of printable characters
            if text_content:
                printable_chars = sum(1 for c in text_content if c in string.printable)
                # Use original byte length for ratio calculation to account for ignored bytes
                printable_ratio = printable_chars / len(content)
                
                # Adjust ratio threshold based on decoding success
                threshold = 0.7 if decoding_success else 0.5
                is_binary = printable_ratio < threshold
                
                confidence = 'high' if printable_ratio > 0.9 or printable_ratio < 0.3 else 'medium'
                if not decoding_success:
                    confidence = 'medium' if confidence == 'high' else 'low'
                
                return {
                    'is_binary': is_binary,
                    'mime': None,
                    'confidence': confidence
                }
            else:
                # Empty after decode, probably binary
                return {
                    'is_binary': True,
                    'mime': None,
                    'confidence': 'medium'
                }
                
        except UnicodeDecodeError:
            # Failed to decode as UTF-8, likely binary
            return {
                'is_binary': True,
                'mime': None,
                'confidence': 'high'
            }
            
    except subprocess.CalledProcessError:
        # Can't get blob content, assume binary
        return {
            'is_binary': True,
            'mime': None,
            'confidence': 'low'
        }


def detect_blob_type(blob_sha: str) -> Dict[str, any]:
    """
    Detect if a blob is binary or text using available methods.
    
    First tries OS file command, falls back to content heuristics.
    
    Args:
        blob_sha: SHA hash of the blob
        
    Returns:
        Dictionary with keys:
        - is_binary: bool - True if binary, False if text
        - mime: Optional[str] - MIME type if detected
        - confidence: str - 'high', 'medium', or 'low'
    """
    if not blob_sha or not blob_sha.strip():
        return {
            'is_binary': True,
            'mime': None,
            'confidence': 'low'
        }
    
    # Try file command first
    file_result = detect_type_with_file_command(blob_sha)
    if file_result is not None:
        return file_result
    
    # Fallback to content heuristics
    return detect_type_with_content_heuristics(blob_sha)


def detect_blob_types_batch(blob_shas: list[str]) -> Dict[str, Dict[str, any]]:
    """
    Detect types for multiple blobs.
    
    Args:
        blob_shas: List of blob SHA hashes
        
    Returns:
        Dictionary mapping blob SHA to type detection results
    """
    results = {}
    
    for blob_sha in blob_shas:
        if blob_sha and blob_sha.strip():
            # First verify the blob exists by trying to get its content
            try:
                subprocess.run(
                    ['git', 'cat-file', '-e', blob_sha],
                    capture_output=True,
                    check=True
                )
                # If blob exists, detect its type
                results[blob_sha] = detect_blob_type(blob_sha)
            except subprocess.CalledProcessError:
                # Skip blobs that don't exist
                continue
    
    return results


def augment_blobs_with_types(blobs: list[Dict[str, any]]) -> list[Dict[str, any]]:
    """
    Augment blob records with type detection information.
    
    Args:
        blobs: List of blob dictionaries
        
    Returns:
        List of blob dictionaries with added type detection fields:
        - is_binary, mime_type, type_confidence
    """
    # Extract unique blob SHAs (excluding empty ones for deleted files)
    unique_blob_shas = set()
    for blob in blobs:
        if blob.get('blob_sha'):
            unique_blob_shas.add(blob['blob_sha'])
    
    # Detect types for all unique blobs
    types = detect_blob_types_batch(list(unique_blob_shas))
    
    # Augment the original blob records
    augmented_blobs = []
    for blob in blobs:
        augmented_blob = blob.copy()
        blob_sha = blob.get('blob_sha')
        
        if blob_sha and blob_sha in types:
            type_info = types[blob_sha]
            augmented_blob['is_binary'] = type_info['is_binary']
            augmented_blob['mime_type'] = type_info['mime']
            augmented_blob['type_confidence'] = type_info['confidence']
        else:
            # For deleted files or errors
            augmented_blob['is_binary'] = None
            augmented_blob['mime_type'] = None
            augmented_blob['type_confidence'] = None
            
        augmented_blobs.append(augmented_blob)
    
    return augmented_blobs