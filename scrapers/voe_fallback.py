"""
VOE.sx fallback downloader using yt-dlp.
"""

import os
import logging
import yt_dlp

class VoeFallbackDownloader:
    """
    Fallback downloader for VOE.sx videos when other methods fail.
    Uses yt-dlp to download videos from VOE.sx.
    """

    def __init__(self):
        """Initialize the downloader."""
        logging.info("VoeFallbackDownloader initialized")

    def download_video(self, url, output_path):
        """
        Download a video from VOE.sx.

        Args:
            url (str): The VOE.sx URL
            output_path (str): Path to save the video

        Returns:
            bool: True if successful, False otherwise
        """
        logging.info(f"VoeFallbackDownloader: Downloading {url} to {output_path}")

        try:
            # Make sure the output directory exists
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            # Configure yt-dlp options
            ydl_opts = {
                'format': 'best',
                'outtmpl': output_path,
                'quiet': False,
                'no_warnings': False,
                'extractor_args': {'youtube': {'player_skip': ['js', 'configs', 'webpage']}},
            }

            # Download the video
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])

            # Check if the file was downloaded
            if os.path.exists(output_path):
                logging.info(f"VoeFallbackDownloader: Successfully downloaded to {output_path}")
                return True
            else:
                logging.error(f"VoeFallbackDownloader: File not found after download: {output_path}")
                return False

        except Exception as e:
            logging.error(f"VoeFallbackDownloader: Error downloading {url}: {str(e)}")
            return False
