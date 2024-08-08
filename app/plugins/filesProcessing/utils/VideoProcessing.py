import ffprobe3
import ffmpeg
import os

def main(filepath, output):
    try:
        if not os.path.exists(filepath):
            raise Exception('El archivo no existe')
        
        metadata = ffprobe3.FFProbe(filepath)

        print(len(metadata.streams))

        video = False
        audio = False
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

        if audio:
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