# [START app]
# [START imports]
import logging
import tempfile
import os
from flask import Flask, render_template, request, send_from_directory
import sys
import traceback # TODO: remove
import PyPDF4
import zlib
try:
    from PIL import Image, ImageFont, ImageDraw
except ImportError:
    import Image
import struct

# [END imports]
app = Flask(__name__)

def add_margin(pil_img, top, right, bottom, left, text):
    width, height = pil_img.size
    new_width = width + right + left
    new_height = height + top + bottom
    result = Image.new(pil_img.mode, (new_width, new_height), color='white')
    result.paste(pil_img, (left, top))
    draw = ImageDraw.Draw(result)
    font = ImageFont.truetype("courbd.ttf", 46, encoding="unic")
    draw.text((12, result.size[1]-46), text, font=font)
    return result

def makeGrayPNG(raw, width = None, height = None, bitdepth=2):
    def I1(value):
        return struct.pack("!B", value & (2**8-1))
    def I4(value):
        return struct.pack("!I", value & (2**32-1))
    # generate these chunks depending on image type
    makeIHDR = True
    makeIDAT = True
    makeIEND = True
    png = b"\x89" + "PNG\r\n\x1A\n".encode('ascii')
    if makeIHDR:
        colortype = 0 # true gray image (no palette)
        # bitdepth = 1 # with one byte per pixel (0..255)
        compression = 0 # zlib (no choice here)
        filtertype = 0 # adaptive (each scanline seperately)
        interlaced = 0 # no
        IHDR = I4(width) + I4(height) + I1(bitdepth)
        IHDR += I1(colortype) + I1(compression)
        IHDR += I1(filtertype) + I1(interlaced)
        block = "IHDR".encode('ascii') + IHDR
        png += I4(len(IHDR)) + block + I4(zlib.crc32(block))
    if makeIDAT:
        block = "IDAT".encode('ascii') + raw
        png += I4(len(raw)) + block + I4(zlib.crc32(block))
    if makeIEND:
        block = "IEND".encode('ascii')
        png += I4(0) + block + I4(zlib.crc32(block))
    return png


# pypdf4
def extract(input_file):
    labels = {}
    with open(input_file, 'rb') as pdf_file:
        pdf_reader = PyPDF4.PdfFileReader(pdf_file)
        for i in range(pdf_reader.getNumPages()):
            page = pdf_reader.getPage(i)
            if '/XObject' in page['/Resources']:
                try:
                    xObject = page['/Resources']['/XObject'].getObject()
                except:
                    xObject = []

                meta = page.extractText().split('\n')[2::3]
                for obj in xObject:
                    o = xObject[obj]
                    if o['/Subtype'] == '/Image':
                        width, height = o['/Width'], o['/Height']
                        if height == 2200: # USPS Summary Page
                            continue
                        batch = meta.pop(0) if bool(meta) else ''
                        data = o._data
                        try :
                            if o['/ColorSpace'] == '/DeviceRGB':
                                mode = "RGB"
                            elif o['/ColorSpace'] == '/DeviceCMYK':
                                mode = "CMYK" # WONTFIX: Convert to RGB before saving
                            else:
                                mode = "P"
                            fn = "%stmp%sp%03d-%s" % (os.sep, os.sep, i + 1, obj[1:])

                            if '/Filter' in o:

                                if '/FlateDecode' in o['/Filter']:
                                    data = zlib.decompress(data)

                                if '/DCTDecode' in o['/Filter']:
                                    fn += ".jpg"
                                    img = open(fn, "wb")
                                    img.write(data)
                                    img.close()
                                elif '/FlateDecode' == o['/Filter']:
                                    # This is painful...
                                    fn += ".png"
                                    img = open(fn, "wb")
                                    data = makeGrayPNG(raw=o._data, width=width, height=height, bitdepth=o['/BitsPerComponent'])
                                    img.write(data)
                                    img.close()
                                elif '/JPXDecode' in o['/Filter']:
                                    fn += ".jp2"
                                    img = open(fn, "wb")
                                    img.write(data)
                                    img.close()
                                elif '/CCITTFaxDecode' in o['/Filter'] or '/LZWDecode' in o['/Filter']:
                                    fn += ".tiff"
                                    img = open(fn, "wb")
                                    img.write(data)
                                    img.close()
                                else :
                                    logging.error('Unknown format: %s' % o['/Filter'])
                                    continue
                            else: # I doubt this works
                                fn += ".png"
                                img = Image.frombytes(mode, (width, height), data)
                                img.save(fn)
                            # if height < 1801 and width < 1201:
                            labels[fn] = batch
                        except:
                            traceback.print_exc()
                        logging.info(f"{fn}\t{o['/Width']}x{o['/Height']}\t{labels[fn]}")
                        im = Image.open(fn)
                        if height < width:
                            im = im.rotate(-90, expand=True)
                            width, height = height, width
                        im = im.resize((int(280/72*300), int(410/72*300)))
                        if width == 762 and height == 1200:
                            labels[fn] = ''
                        elif height < 1801 and width < 1201:
                            im.save(fn, quality=100)
                            im = add_margin(im, 0, 0, 50, 0, labels[fn])
                        im.save(fn, quality=100)
            # else:
                # print("No image found for page %d" % (i + 1))

    return labels

def glue(labels):
    # rect = fitz.Rect(0, 0, 280, 410)
    images = [Image.open(label).convert('RGB') for label in labels]
    r = "%stmp%s%s.pdf" % (os.sep, os.sep, str(tempfile.TemporaryFile().name).split(os.sep)[-1])
    images[0].save(r, save_all=True, append_images=images[1:])
    for label in labels:
        os.remove(label)
        
    return r

# # fitz
def convert2(input_file):
    import fitz # https://pymupdf.readthedocs.io/en/latest/tutorial/
    pdf = fitz.open(input_file)
    labels = {}
    for no in range(len(pdf)):
        i = 0
        paragraphs = pdf.loadPage(no).getTextBlocks()
        for image in pdf.getPageImageList(no):
            xref = image[0]
            pix = fitz.Pixmap(pdf, xref)
            if pix.n > 4:        # CMYK vs GRAY or RGB
                pix = fitz.Pixmap(fitz.csRGB, pix)
            file = "p%s-i%s.png" % (no, xref)
            pix.writePNG(file)
            pix = None

            img  = Image.open(file)
            width, height = img.size
            if width > height: # Landscape, have to rotate -90
                width, height = height, width
                img = img.rotate(270, expand=True)
                img.save(file)
            if height == 2200: # USPS Summary Page
                continue
            if height < 1801 and width < 1201:
                labels[file] = paragraphs[i*3+2][4]
            if width == 762 and height == 1200:
                labels[file] = ''
            img = None
            i += 1

    doc = fitz.open()
    rect = fitz.Rect(0, 0, 280, 410)
    for label in labels:
        pix = fitz.Pixmap(label)
        page = doc.newPage(width = 282, height = 424)
        page.insertImage(rect, pixmap = pix)

        p1 = fitz.Point(12, page.rect.height - 6)
        shape = page.newShape()
        shape.insertText(p1, labels[label], fontsize = 12)
        shape.commit()
        os.remove(label)
    fn = "%stmp%s%s.pdf" % (os.sep, os.sep, str(tempfile.TemporaryFile().name).split(os.sep)[-1])
    doc.save(fn, garbage=4, deflate=1)
    return fn

@app.route('/labels', methods = ['GET', 'POST'])
def upload():
    if request.method == 'POST':
        f = request.files['file']
        fn = "%stmp%s%s.pdf" % (os.sep, os.sep, str(tempfile.TemporaryFile().name).split(os.sep)[-1])
        f.save(fn)
        try: # PyMuPDF
            
            
            
            
            zzz



            r = convert2(fn)
        except:
            r = glue(extract(fn))
    
    with open(r, 'rb'):
        filename = os.path.basename(r)
        pathname = r[:len(r)-len(filename)]
        return send_from_directory(directory=pathname, filename=filename, mimetype='application/pdf')


# [START upload]
@app.route('/upload')
def uploader():
    return render_template('upload.html')
# [END upload]


# [START form]
@app.route('/')
def form():
    return render_template('upload.html')
# [END form]


# [START submitted]
@app.route('/submitted', methods=['POST'])
def submitted_form():
    name = request.form['name']
    email = request.form['email']
    site = request.form['site_url']
    comments = request.form['comments']

    # [END submitted]
    # [START render_template]
    return render_template(
        'submitted_form.html',
        name=name,
        email=email,
        site=site,
        comments=comments)
    # [END render_template]

@app.route('/favicon.ico')
def favicon():
    return send_from_directory(os.path.join(app.root_path, 'static'),
                               'favicon.ico', mimetype='image/vnd.microsoft.icon')

@app.errorhandler(500)
def server_error(e):
    # Log the error and stacktrace.
    logging.exception('An error occurred during a request.')
    return "An internal error occurred", 500
# [END app]
