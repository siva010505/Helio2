import os
import urllib.request
import textwrap
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter

def create_modern_thumbnail(image_path, output_path, hook_text, emoji_hex):
    # Load Image
    img = Image.open(image_path).convert("RGBA")
    
    # Resize to 1920x1080 to simulate the video frame
    img = img.resize((1920, 1080), Image.LANCZOS)
    
    # Apply YouTube-style Color Grading
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.2)
    enhancer = ImageEnhance.Color(img)
    img = enhancer.enhance(1.4)
    
    W, H = img.size
    
    # Vignette
    vignette = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    v_draw = ImageDraw.Draw(vignette)
    for i in range(250):
        alpha = int(255 * ((250 - i) / 250) * 0.8)
        v_draw.rectangle([i, i, W - i, H - i], outline=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, vignette)
    
    # Tilted Text Layer
    text_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    t_draw = ImageDraw.Draw(text_layer)
    
    font_path = "assets/fonts/Roboto-Bold.ttf"
    try:
        font = ImageFont.truetype(font_path, 190)
    except IOError:
        font = ImageFont.load_default()
        
    wrapped_text = textwrap.fill(hook_text, width=15)
    bbox = t_draw.textbbox((0, 0), wrapped_text, font=font, align="center")
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    
    x = (W - w) // 2
    y = H - h - 180
    
    # Soft drop shadow
    shadow_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow_layer)
    s_draw.text((x, y), wrapped_text, font=font, fill=(0, 0, 0, 255), align="center")
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(30))
    
    text_layer = Image.alpha_composite(text_layer, shadow_layer)
    t_draw = ImageDraw.Draw(text_layer)
    
    # Thick black stroke
    outline_range = 6
    for dx in range(-outline_range, outline_range+1, 2):
        for dy in range(-outline_range, outline_range+1, 2):
            t_draw.text((x+dx, y+dy), wrapped_text, font=font, fill="black", align="center")
            
    # Main text (Yellow to make it pop)
    t_draw.text((x, y), wrapped_text, font=font, fill="#FFD400", align="center")
    
    # Rotate text layer
    text_layer = text_layer.rotate(4, resample=Image.BICUBIC, center=(W//2, H - 200))
    final_img = Image.alpha_composite(img, text_layer).convert("RGB")
    
    # Download and Add Emoji
    emoji_path = f"{emoji_hex}.png"
    if not os.path.exists(emoji_path):
        try:
            req = urllib.request.Request(
                f"https://raw.githubusercontent.com/twitter/twemoji/master/assets/72x72/{emoji_hex}.png", 
                headers={'User-Agent': 'Mozilla/5.0'}
            )
            with urllib.request.urlopen(req) as response, open(emoji_path, 'wb') as f:
                f.write(response.read())
        except Exception as e:
            print("Failed to download emoji:", e)
            
    if os.path.exists(emoji_path):
        try:
            emoji = Image.open(emoji_path).convert("RGBA")
            emoji = emoji.resize((350, 350), Image.LANCZOS)
            emoji = emoji.rotate(-15, expand=True)
            ew, eh = emoji.size
            final_img.paste(emoji, (W - ew - 150, H - eh - 100), mask=emoji)
        except Exception as e:
            pass
    
    final_img.save(output_path, "JPEG", quality=95)
    return output_path

def main():
    print("Downloading historical dancing plague image for realistic test...")
    image_path = "dancing_bg.jpg"
    if not os.path.exists(image_path):
        try:
            # Fetching a reliable stock image for the test
            url = "https://picsum.photos/1920/1080"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response, open(image_path, 'wb') as f:
                f.write(response.read())
        except Exception as e:
            print("Failed to download image:", e)
            return

    # In the actual pipeline, the LLM will generate a hook like this based on the title!
    hook = "DANCED TO\nDEATH!"
    
    # 1f480 is the Skull Emoji, highly relevant to the "plague/death" aspect
    emoji_hex = "1f480"
    
    out_path = "dancing_plague_thumbnail.jpg"
    print(f"Generating modern thumbnail for: The Strange Story of the Dancing Plague")
    create_modern_thumbnail(image_path, out_path, hook, emoji_hex)
    print(f"SUCCESS! Open this file: {os.path.abspath(out_path)}")

if __name__ == "__main__":
    main()
