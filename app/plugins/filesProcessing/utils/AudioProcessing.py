import ffmpeg


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