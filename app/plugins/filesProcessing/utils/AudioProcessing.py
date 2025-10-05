import ffmpeg

def main(filepath, output):
    try:
        output_mp3 = output + '.mp3'
        (
            ffmpeg
            .input(filepath)
            .output(output_mp3, acodec='libmp3lame', ab='128k')
            .overwrite_output()
            .run()
        )

        output_ogg = output + '.ogg'
        (
            ffmpeg
            .input(filepath)
            .output(output_ogg, acodec='libvorbis', **{'q:a': 4})
            .overwrite_output()
            .run()
        )

        return True
    except Exception as e:
        raise Exception('Error al convertir el archivo: ' + str(e))