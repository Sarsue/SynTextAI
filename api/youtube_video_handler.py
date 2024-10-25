import yt-dlp
import os

def download_video(video_url):
    """Download video from a given URL."""
    ydl_opts = {
        'format': 'best',  # Get the best quality video
        'outtmpl': '%(title)s.%(ext)s',  # Save with video title as filename
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',  # Convert to mp4 format
        }],
        'quiet': False,  # Change to True to suppress output
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([video_url])
            print(f"Downloaded video from: {video_url}")
    except Exception as e:
        print(f"Error downloading video: {e}")

if __name__ == '__main__':
    video_urls = [
        'https://www.youtube.com/watch?v=jYDQd2Czgkg',
    ]

    for url in video_urls:
        download_video(url)
