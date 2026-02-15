import ffprobe3
import ffmpeg
import os


def get_metadata(filepath):
    try:
        probe = ffmpeg.probe(filepath)
    except Exception:
        return {}

    fmt = probe.get('format', {})
    duration = fmt.get('duration')
    bit_rate = fmt.get('bit_rate')
    metadata = {}
    if duration:
        try:
            metadata['duration_ms'] = int(float(duration) * 1000)
        except (TypeError, ValueError):
            pass
    if bit_rate:
        try:
            metadata['bit_rate'] = int(float(bit_rate))
        except (TypeError, ValueError):
            pass
    return metadata

def main(filepath, output):
    try:
        if not os.path.exists(filepath):
            raise Exception('El archivo no existe')
        
        video = False
        audio = False

        try:
            metadata = ffprobe3.FFProbe(filepath)
        except:
            metadata = None
            video = True
        
        if metadata:
            for stream in metadata.streams:
                if stream.is_video():
                    video = True
                if stream.is_audio():
                    audio = True

        if video:
            output_file = output + ".mp4"
            (
                ffmpeg
                .input(filepath)
                .output(output_file, vcodec='libx264', acodec='aac', vf='scale=480:trunc(ow/a/2)*2')
                .overwrite_output()
                .run()
            )

            output_file = output + ".webm"
            (
                ffmpeg
                .input(filepath)
                .output(output_file, vcodec='libvpx', acodec='libvorbis', vf='scale=480:trunc(ow/a/2)*2')
                .overwrite_output()
                .run()
            )

        if audio and not video:
            output_file = output + ".mp3"
            (
                ffmpeg
                .input(filepath)
                .output(output_file, acodec='libmp3lame', ab='128k')
                .overwrite_output()
                .run()
            )

            output_file = output + ".ogg"
            (
                ffmpeg
                .input(filepath)
                .output(output_file, acodec='libvorbis', ab='128k')
                .overwrite_output()
                .run()
            )

        return audio, video
    except Exception as e:
        print(str(e))
        raise Exception('Error al convertir el archivo: ' + str(e))