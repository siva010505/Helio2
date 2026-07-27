import os
import urllib.request
import textwrap
from PIL import Image, ImageDraw, ImageFont, ImageEnhance, ImageFilter

def create_modern_thumbnail(image_path, output_path, hook_text):
    # 1. Load Image and Apply YouTube-style Color Grading
    img = Image.open(image_path).convert("RGBA")
    
    # Boost contrast and saturation
    enhancer = ImageEnhance.Contrast(img)
    img = enhancer.enhance(1.3)
    enhancer = ImageEnhance.Color(img)
    img = enhancer.enhance(1.5)
    
    W, H = img.size
    
    # 2. Add a vignette (darkened edges) to focus attention on the center
    vignette = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    v_draw = ImageDraw.Draw(vignette)
    for i in range(200):
        alpha = int(255 * ((200 - i) / 200) * 0.7)
        v_draw.rectangle([i, i, W - i, H - i], outline=(0, 0, 0, alpha))
    img = Image.alpha_composite(img, vignette)
    
    # 3. Render Dynamic Tilted Text
    # We create a separate transparent image for the text so we can rotate it
    text_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    t_draw = ImageDraw.Draw(text_layer)
    
    # Try to load a font, fallback to default
    font_path = "assets/fonts/Roboto-Bold.ttf"
    try:
        font = ImageFont.truetype(font_path, 180)
    except IOError:
        font = ImageFont.load_default()
        
    wrapped_text = textwrap.fill(hook_text, width=15)
    
    # Calculate text size
    bbox = t_draw.textbbox((0, 0), wrapped_text, font=font, align="center")
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    
    x = (W - w) // 2
    y = H - h - 150  # Near bottom
    
    # Draw a soft drop shadow (glow)
    shadow_layer = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    s_draw = ImageDraw.Draw(shadow_layer)
    s_draw.text((x, y), wrapped_text, font=font, fill=(0, 0, 0, 255), align="center")
    shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(25))
    
    # Merge shadow onto text layer
    text_layer = Image.alpha_composite(text_layer, shadow_layer)
    t_draw = ImageDraw.Draw(text_layer)
    
    # Draw text with a modern thin stroke
    outline_range = 5
    for dx in range(-outline_range, outline_range+1, 2):
        for dy in range(-outline_range, outline_range+1, 2):
            t_draw.text((x+dx, y+dy), wrapped_text, font=font, fill="black", align="center")
            
    # Draw main text in white for a clean, modern look
    t_draw.text((x, y), wrapped_text, font=font, fill="white", align="center")
    
    # Rotate the text layer by -4 degrees for a dynamic, energetic feel
    text_layer = text_layer.rotate(4, resample=Image.BICUBIC, center=(W//2, H - 200))
    
    # Composite text onto image
    final_img = Image.alpha_composite(img, text_layer).convert("RGB")
    
    # Add a glowing red arrow or circle? (We can fetch an emoji overlay)
    emoji_path = "shock_emoji.png"
    if not os.path.exists(emoji_path):
        try:
            # Download a high-res shock emoji from an open-source CDN
            urllib.request.urlretrieve("https://raw.githubusercontent.com/twitter/twemoji/master/assets/72x72/1f92f.png", emoji_path)
        except:
            pass
            
    if os.path.exists(emoji_path):
        try:
            emoji = Image.open(emoji_path).convert("RGBA")
            emoji = emoji.resize((350, 350), Image.LANCZOS)
            # Paste it slightly rotated
            emoji = emoji.rotate(-15, expand=True)
            ew, eh = emoji.size
            # Paste near the bottom right of the text
            final_img.paste(emoji, (W - ew - 150, H - eh - 100), mask=emoji)
        except Exception as e:
            print("Failed to add emoji:", e)
    
    final_img.save(output_path, "JPEG", quality=95)
    return output_path

def main():
    print("Downloading a random high-quality stock photo for a realistic test...")
    image_path = "test_bg.jpg"
    try:
        # Fetch a random 1920x1080 image from Picsum
        req = urllib.request.Request("https://picsum.photos/1920/1080", headers={'User-Agent': 'Mozilla/5.0'})
        with urllib.request.urlopen(req) as response:
            with open(image_path, 'wb') as f:
                f.write(response.read())
    except Exception as e:
        print("Failed to download image:", e)
        # Create a dummy image
        img = Image.new('RGB', (1920, 1080), color=(50, 50, 100))
        img.save(image_path)
        
    hook = "THEY HID THIS\nFROM YOU!"
    out_path = "modern_thumbnail.jpg"
    print("Generating modern thumbnail...")
    create_modern_thumbnail(image_path, out_path, hook)
    print(f"SUCCESS! Open this file: {os.path.abspath(out_path)}")

if __name__ == "__main__":
    main()
