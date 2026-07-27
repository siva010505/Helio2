import os
import requests
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter

def create_documentary_thumbnail(image_path, output_path, line1_text, highlighted_text):
    # Load Image
    img = Image.open(image_path).convert("RGBA")
    
    # Resize and crop to 1920x1080 (Cover)
    target_ratio = 1920 / 1080
    w, h = img.size
    if w / h > target_ratio:
        # Crop width
        new_w = int(h * target_ratio)
        offset = (w - new_w) // 2
        img = img.crop((offset, 0, offset + new_w, h))
    else:
        # Crop height
        new_h = int(w / target_ratio)
        offset = (h - new_h) // 2
        img = img.crop((0, offset, w, offset + new_h))
        
    img = img.resize((1920, 1080), Image.LANCZOS)
    
    # Subtle color grading (not too aggressive, documentary style)
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.15)
    enhancer = ImageEnhance.Color(img)
    img = enhancer.enhance(1.2)
    
    W, H = img.size
    
    # Add a soft vignette (darkened edges) to focus attention
    vignette = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    v_draw = ImageDraw.Draw(vignette)
    for i in range(300):
        alpha = int(255 * ((300 - i) / 300) * 0.7)
        v_draw.rectangle([i, i, W - i, H - i], outline=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, vignette)
    
    # Draw Text
    draw = ImageDraw.Draw(img)
    
    font_path = "assets/fonts/Roboto-Bold.ttf"
    try:
        font = ImageFont.truetype(font_path, 160)
    except IOError:
        font = ImageFont.load_default()
        
    # Function to get exact text height
    def get_text_dimensions(text, font):
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox[2] - bbox[0], bbox[3] - bbox[1], bbox[1]
        
    w1, h1, offset1 = get_text_dimensions(line1_text, font)
    w2, h2, offset2 = get_text_dimensions(highlighted_text, font)
    
    margin = 20 # space between lines
    pad_x = 50
    pad_y = 20
    
    box_w = w2 + (pad_x * 2)
    box_h = h2 + (pad_y * 2)
    
    total_h = h1 + margin + box_h
    
    # Position at bottom center (similar to the Jack Neel / DOAC style)
    start_y = H - total_h - 100
    
    # Line 1
    x1 = (W - w1) // 2
    y1 = start_y
    
    # Drop shadow for line 1
    draw.text((x1+8, y1+8 - offset1), line1_text, font=font, fill=(0,0,0, 200))
    draw.text((x1, y1 - offset1), line1_text, font=font, fill="white")
    
    # Line 2 (Red Highlight Box)
    x2 = (W - box_w) // 2
    y2 = y1 + h1 + margin
    
    # Box Drop Shadow
    draw.rectangle([x2+10, y2+10, x2+box_w+10, y2+box_h+10], fill=(0,0,0,150))
    # Red Box
    draw.rectangle([x2, y2, x2+box_w, y2+box_h], fill="#D90000")
    
    # Text inside box
    text_x = x2 + pad_x
    text_y = y2 + pad_y - offset2
    draw.text((text_x, text_y), highlighted_text, font=font, fill="white")
    
    final_img = img.convert("RGB")
    final_img.save(output_path, "JPEG", quality=95)
    return output_path

def main():
    print("Downloading historical dancing plague image using requests...")
    image_path = "bruegel_bg.jpg"
    if not os.path.exists(image_path):
        try:
            # Using a reliable public Unsplash URL for dancing/party
            url = "https://images.unsplash.com/photo-1547153760-18fc86324498?ixlib=rb-4.0.3&auto=format&fit=crop&w=1920&q=80"
            response = requests.get(url, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'})
            response.raise_for_status()
            with open(image_path, 'wb') as f:
                f.write(response.content)
        except Exception as e:
            print("Failed to download image:", e)
            return

    # Using the exact style requested by the user: White text with a RED BOX highlight
    line1 = "they danced to"
    highlight = "DEATH"
    
    out_path = "documentary_thumbnail.jpg"
    print(f"Generating documentary-style thumbnail...")
    create_documentary_thumbnail(image_path, out_path, line1, highlight)
    print(f"SUCCESS! Open this file: {os.path.abspath(out_path)}")

if __name__ == "__main__":
    main()
