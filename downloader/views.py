from django.shortcuts import render, redirect
from pytube import YouTube
from pytube.exceptions import VideoUnavailable, RegexMatchError, PytubeError, AgeRestrictedError
from django.http import HttpResponse, HttpResponseBadRequest
import os
from django.conf import settings
import logging
import re
from urllib.parse import urlparse, parse_qs
from datetime import timedelta
import time

# Setting the custom User-Agent in YouTube initialization (No need to modify internal headers)
logger = logging.getLogger(__name__)

def sanitize_youtube_url(url):
    """Extracts video ID from various YouTube URL formats"""
    patterns = [
        r"(?:https?:\/\/)?(?:www\.|m\.)?youtu\.be\/([a-zA-Z0-9_-]{11})",
        r"(?:https?:\/\/)?(?:www\.|m\.)?youtube\.com\/watch\?v=([a-zA-Z0-9_-]{11})",
        r"(?:https?:\/\/)?(?:www\.|m\.)?youtube\.com\/embed\/([a-zA-Z0-9_-]{11})",
        r"(?:https?:\/\/)?(?:www\.|m\.)?youtube\.com\/v\/([a-zA-Z0-9_-]{11})"
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match and len(match.group(1)) == 11:
            return f"https://www.youtube.com/watch?v={match.group(1)}"
    
    raise RegexMatchError("Invalid YouTube URL format")

def format_duration(seconds):
    """Convert duration in seconds to HH:MM:SS format"""
    return str(timedelta(seconds=seconds))

def get_yt_object(url, max_retries=3):
    """Create YouTube object with retry logic"""
    for attempt in range(max_retries):
        try:
            # Passing User-Agent directly when creating YouTube object
            yt = YouTube(
                url,
                headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Safari/537.36 Edg/112.0.1722.68"},
                on_progress_callback=None,
                on_complete_callback=None
            )
            yt.bypass_age_gate()  # Bypass age restrictions
            yt.vid_info  # Trigger metadata request
            return yt
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1} failed: {str(e)}")
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            continue

    raise PytubeError("Could not initialize YouTube object. Check your network or try another video.")

def home(request):
    context = {}
    if request.method == 'POST':
        url = request.POST.get('url', '').strip()
        if not url:
            context['error'] = "Please enter a YouTube URL"
            return render(request, 'home.html', context)
            
        try:
            clean_url = sanitize_youtube_url(url)
            yt = get_yt_object(clean_url)
            
            # Get available streams
            streams = yt.streams.filter(
                progressive=True,
                file_extension='mp4'
            ).order_by('resolution').desc()
            
            context.update({
                'title': yt.title,
                'duration': format_duration(yt.length),
                'views': "{:,}".format(yt.views),
                'author': yt.author,
                'thumbnail': yt.thumbnail_url,
                'streams': streams,
                'video_id': yt.video_id,
                'clean_url': clean_url,
                'success': True
            })
            
        except PytubeError as e:
            context['error'] = str(e)
        except AgeRestrictedError:
            context['error'] = "Age-restricted video. Please login to YouTube."
        except RegexMatchError:
            context['error'] = "Invalid YouTube URL format"
        except Exception as e:
            logger.error(f"Error processing URL {url}: {str(e)}", exc_info=True)
            context['error'] = "Failed to fetch video information"
    
    return render(request, 'home.html', context)

def download_video(request):
    if request.method == 'POST':
        url = request.POST.get('url', '').strip()
        quality = request.POST.get('quality', 'highest')
        
        if not url:
            return HttpResponseBadRequest("No URL provided")
            
        try:
            clean_url = sanitize_youtube_url(url)
            yt = get_yt_object(clean_url)
            
            if quality == 'highest':
                stream = yt.streams.get_highest_resolution()
            elif quality == 'lowest':
                stream = yt.streams.get_lowest_resolution()
            else:
                stream = yt.streams.get_by_resolution(quality)
                
            if not stream:
                raise PytubeError("No suitable stream found")
            
            downloads_dir = os.path.join(settings.MEDIA_ROOT, 'downloads')
            os.makedirs(downloads_dir, exist_ok=True)
            
            safe_title = "".join(c for c in yt.title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            filename = f"{safe_title}_{yt.video_id}.mp4"
            filepath = os.path.join(downloads_dir, filename)
            
            stream.download(
                output_path=downloads_dir,
                filename=filename,
                timeout=30
            )
            
            if os.path.exists(filepath):
                with open(filepath, 'rb') as f:
                    response = HttpResponse(f.read(), content_type='video/mp4')
                    response['Content-Disposition'] = f'attachment; filename="{filename}"'
                    return response
            raise FileNotFoundError("Downloaded file not found")
                
        except PytubeError as e:
            return render(request, 'home.html', {'error': str(e), 'url': url})
        except Exception as e:
            logger.error(f"Download failed for {url}: {str(e)}", exc_info=True)
            return render(request, 'home.html', {
                'error': 'Download failed. Please try again later.',
                'url': url
            })
    
    return redirect('home')
