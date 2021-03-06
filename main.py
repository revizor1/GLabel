# [START app]
# [START imports]
import logging
import tempfile
import os
import struct
import traceback
import zlib
import PyPDF4
from flask import Flask, render_template, request, send_from_directory

try:
    from PIL import Image, ImageFont, ImageDraw
except ImportError:
    import Image

# [END imports]
app = Flask(__name__)
resolution = 200  # 300 is slow to print


def add_margin(pil_img, top, right, bottom, left, text):
    width, height = pil_img.size
    new_width, new_height = width + right + left, height + top + bottom
    result = Image.new(pil_img.mode, (new_width, new_height), color="white")
    result.paste(pil_img, (left, top))
    draw = ImageDraw.Draw(result)
    fontsize = int(0.15 * resolution)
    draw.text((12, result.size[1] - 46), text, font=ImageFont.truetype("courbd.ttf", fontsize, encoding="unic"))
    return result


def make_gray_png(raw, width=None, height=None, bitdepth=2):
    def I1(value):
        return struct.pack("!B", value & (2 ** 8 - 1))

    def I4(value):
        return struct.pack("!I", value & (2 ** 32 - 1))

    # generate these chunks depending on image type
    makeIHDR = True
    makeIDAT = True
    makeIEND = True
    png = b"\x89" + "PNG\r\n\x1A\n".encode("ascii")
    if makeIHDR:
        colortype = 0  # true gray image (no palette)
        # bitdepth = 1 # with one byte per pixel (0..255)
        compression = 0  # zlib (no choice here)
        filtertype = 0  # adaptive (each scanline seperately)
        interlaced = 0  # no
        IHDR = I4(width) + I4(height) + I1(bitdepth)
        IHDR += I1(colortype) + I1(compression)
        IHDR += I1(filtertype) + I1(interlaced)
        block = "IHDR".encode("ascii") + IHDR
        png += I4(len(IHDR)) + block + I4(zlib.crc32(block))
    if makeIDAT:
        block = "IDAT".encode("ascii") + raw
        png += I4(len(raw)) + block + I4(zlib.crc32(block))
    if makeIEND:
        block = "IEND".encode("ascii")
        png += I4(0) + block + I4(zlib.crc32(block))
    return png


# pypdf4
def extract(input_file):
    labels = {}
    with open(input_file, "rb") as pdf_file:
        pdf_reader = PyPDF4.PdfFileReader(pdf_file)
        for i in range(pdf_reader.getNumPages()):
            page = pdf_reader.getPage(i)
            if "/XObject" in page["/Resources"]:
                try:
                    xObject = page["/Resources"]["/XObject"].getObject()
                except:
                    xObject = []

                meta = page.extractText().split("\n")[2::3]
                for obj in xObject:
                    o = xObject[obj]
                    if o["/Subtype"] == "/Image":
                        width, height = o["/Width"], o["/Height"]
                        if width > height:
                            width, height = height, width
                        if height == 2200:  # USPS Summary Page
                            continue
                        batch = meta.pop(0) if bool(meta) else ""
                        data = o._data
                        try:
                            if o["/ColorSpace"] == "/DeviceRGB":
                                mode = "RGB"
                            elif o["/ColorSpace"] == "/DeviceCMYK":
                                mode = "CMYK"  # WONTFIX: Convert to RGB before saving
                            else:
                                mode = "P"
                            fn = "%stmp%sp%03d-%s" % (os.sep, os.sep, i + 1, obj[1:])

                            if "/Filter" in o:
                                if "/FlateDecode" == o["/Filter"]:
                                    # This is painful...
                                    fn += ".png"
                                    data = make_gray_png(
                                        raw=data, width=width, height=height, bitdepth=o["/BitsPerComponent"],
                                    )
                                    open(fn, "wb").write(data)
                                else:
                                    if "/FlateDecode" in o["/Filter"]:
                                        data = zlib.decompress(data)

                                    if "/DCTDecode" in o["/Filter"]:
                                        fn += ".jpg"
                                        open(fn, "wb").write(data)
                                    elif "/JPXDecode" in o["/Filter"]:
                                        fn += ".jp2"
                                        open(fn, "wb").write(data)
                                    elif "/CCITTFaxDecode" in o["/Filter"] or "/LZWDecode" in o["/Filter"]:
                                        fn += ".tiff"
                                        open(fn, "wb").write(data)
                                    else:
                                        logging.error("Unknown format: %s" % o["/Filter"])
                                        continue
                            else:  # I doubt this works
                                fn += ".png"
                                logging.warning(f"This should not be working: {fn}")
                                img = Image.frombytes(mode, (width, height), data)
                                img.save(fn)
                            # if height < 1801 and width < 1201:
                            labels[fn] = batch
                        except:
                            logging.error(traceback.print_exc())
                        prep_image(fn, labels[fn], height, width)
            else:
                logging.warning(f"No images on page {i+1}")

    return labels


def prep_image(fn, label, height, width):
    im = Image.open(fn)
    # logging.warning(f"{fn} \t{im.format}\tmode {im.mode}  \t{o['/Width']}x{o['/Height']}\t{label}")
    logging.warning(f"{fn} \t{im.format}\tmode {im.mode}  \t{label}")
    if (height < width) != (im.height < im.width):
        # if height < width:  # != (im.height < im.width):
        im = im.rotate(-90, expand=True)
        width, height = height, width
    if im.mode != "L":
        im = im.convert("L")  # TODO: tweak dither parameter
    im = im.resize((int(280 / 72 * resolution), int(400 / 72 * resolution)), Image.ANTIALIAS)
    if width != 762 or height != 1200:  # Already labeled
        im = add_margin(im, 0, 0, 50, 0, label)
    im.save(fn, quality=100, subsampling=0, dpi=(resolution, resolution))


def glue(labels):
    logging.debug(f"Processing: {list(labels.keys())}")
    images = [Image.open(label) for label in labels]

    for v in labels.values():
        if v:
            bn = v.split()[0]
            break
    else:
        bn = str(tempfile.TemporaryFile().name).split(os.sep)[-1]
    r = "%stmp%s%s.pdf" % (os.sep, os.sep, bn)
    logging.info(f"Saving {r}")
    try:
        images[0].save(
            r,
            save_all=True,
            append_images=images[1:],
            quality=100,
            subsampling=0,
            resolution=resolution,
            dpi=(resolution, resolution),
        )
    except:
        logging.error(traceback.print_exc())
    for label in labels:
        logging.debug(f"Removing {label}")
        try:
            os.remove(label)
        except:
            logging.warn(f"Cannot remove {label}")
    logging.info(f"Saved {r}")
    return r


# # fitz
def convert2(input_file):
    import fitz  # https://pymupdf.readthedocs.io/en/latest/tutorial/

    pdf = fitz.open(input_file)
    labels = {}
    for no in range(len(pdf)):
        i = 0
        paragraphs = pdf.loadPage(no).getTextBlocks()
        for image in pdf.getPageImageList(no):
            xref = image[0]
            pix = fitz.Pixmap(pdf, xref)
            if pix.n > 4:  # CMYK vs GRAY or RGB
                pix = fitz.Pixmap(fitz.csRGB, pix)
            file = "p%s-i%s.png" % (no, xref)
            pix.writePNG(file)
            pix = None

            img = Image.open(file)
            width, height = img.size
            if width > height:  # Landscape, have to rotate -90
                width, height = height, width
                img = img.rotate(270, expand=True)
                img.save(file, quality=100, subsampling=0)
            if height == 2200:  # USPS Summary Page
                continue
            if height < 1801 and width < 1201:
                labels[file] = paragraphs[i * 3 + 2][4]
            if width == 762 and height == 1200:
                labels[file] = ""
            img = None
            i += 1

    doc = fitz.open()
    rect = fitz.Rect(0, 0, 280, 410)
    for label in labels:
        pix = fitz.Pixmap(label)
        page = doc.newPage(width=282, height=424)
        page.insertImage(rect, pixmap=pix)

        p1 = fitz.Point(12, page.rect.height - 6)
        shape = page.newShape()
        shape.insertText(p1, labels[label], fontsize=12)
        shape.commit()
        os.remove(label)
    fn = "%stmp%s%s.pdf" % (os.sep, os.sep, str(tempfile.TemporaryFile().name).split(os.sep)[-1],)
    doc.save(fn, garbage=4, deflate=1)
    return fn


@app.route("/labels", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        if not request.files["file"]:  # No file provided
            return render_template("upload.html")
        else:
            ext = request.files["file"].filename.split(".")[-1].lower()
            logging.warning(f"Uploading {ext}")
            fn = "%stmp%s%s.%s" % (os.sep, os.sep, str(tempfile.TemporaryFile().name).split(os.sep)[-1], ext)
            request.files["file"].save(fn)
            if ext == "pdf":
                try:
                    r = glue(extract(fn))
                except:
                    logging.warning(f"Plan A failed. Switching to Plan B.")
                    r = convert2(fn)  # PyMuPDF - good luck loading it on Linux
            elif ext in ["jpg", "jpeg", "png"]:
                labels = {fn: ""}
                prep_image(fn, labels[fn], 1200, 800)
                r = glue(labels)

    logging.info(f"Opening {r}")
    with open(r, "rb"):
        filename = os.path.basename(r)
        pathname = r[: len(r) - len(filename)]
        logging.warning(f"Serving {filename}")
        return send_from_directory(directory=pathname, filename=filename, mimetype="application/pdf")


# [START upload]
@app.route("/upload")
def uploader():
    return render_template("upload.html")


# [END upload]


# [START form]
@app.route("/")
def form():
    return render_template("upload.html")


# [END form]


# [START submitted]
@app.route("/submitted", methods=["POST"])
def submitted_form():
    name = request.form["name"]
    email = request.form["email"]
    site = request.form["site_url"]
    comments = request.form["comments"]

    # [END submitted]
    # [START render_template]
    return render_template("submitted_form.html", name=name, email=email, site=site, comments=comments)
    # [END render_template]


@app.route("/favicon.ico")
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, "static"), "favicon.ico", mimetype="image/vnd.microsoft.icon",
    )


@app.errorhandler(500)
def server_error(e):
    # Log the error and stacktrace.
    logging.exception(f"An error occurred during a request. {e}")
    return "An internal error occurred", 500


# [END app]
