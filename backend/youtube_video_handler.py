

def extract_video_id(video_id_or_url):
    if len(video_id_or_url) > 11:
        return video_id_or_url[-11:]
    else:
        return video_id_or_url


def format_duration(seconds):
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{int(hours):02d}:{int(minutes):02d}:{int(seconds):02d}"


def format_transcript(transcript):
    return ' '.join(entry['text'] for entry in transcript)


def process_youtube_link(video_url_or_id):
    try:
        video_id = extract_video_id(video_url_or_id)
        raw_transcript = YouTubeTranscriptApi.get_transcript(video_id)
        formatted_transcript = format_transcript(raw_transcript)
        return formatted_transcript
    except Exception as e:
        print(f"Error: {e}")
        return None


if __name__ == '__main__':
    video_urls = ['https://www.youtube.com/watch?v=l-N1Mb3xTi8',
                  'https://www.youtube.com/watch?v=sIs2vo5r-Xw', 'https://www.youtube.com/watch?v=Vsb8n708Aro']

    for video_url in video_urls:
        try:
            transcript = process_youtube_link(video_url)
            print(transcript)

            # Uncomment the following lines if you want to save the transcript to a file
            # with open(extract_video_id(video_url) + '.txt', 'w') as file:
            #     file.write(transcript)
        except Exception as e:
            print(f"Error processing {video_url}: {e}")
            continue
