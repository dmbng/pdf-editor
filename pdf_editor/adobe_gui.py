"""
Adobe Acrobat-Style Interactive PDF Editor GUI Application.
Features:
- Seamless click-and-drag text repositioning & word-aligned copy-paste selection highlighting.
- Direct keyboard typing replacement & Backspace erase for highlighted text.
- Interactive click-and-drag image repositioning (move images freely anywhere on page).
- Font family (Helvetica, Times, Courier) & font size (8-72pt) controls.
- Add & place images (PNG, JPG, BMP) on PDF pages.
- Page thumbnails sidebar with instant page deletion and rotation.
"""

import os
import sys
from pathlib import Path
from typing import Optional, List, Tuple, Dict, Any
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
import pymupdf as fitz
from PIL import Image, ImageTk

from pdf_editor.core.utils import validate_pdf, PDFEditorError
from pdf_editor.core.merger import merge_pdfs
from pdf_editor.core.splitter import split_by_ranges, split_to_individual_pages

# Set theme
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")


class AdobePDFEditorApp(ctk.CTk):
    def __init__(self, initial_pdf: Optional[str] = None):
        super().__init__()

        self.title("PDF Editor Pro - Visual Interactive Workspace")
        self.geometry("1280x820")
        self.minsize(1050, 700)

        # State Variables
        self.doc_path: Optional[str] = None
        self.doc: Optional[fitz.Document] = None
        self.current_page_idx: int = 0
        self.zoom_scale: float = 1.0
        self.active_mode: str = "select_edit"  # "select_edit", "add_text", "add_image"

        # Selection & Drag State
        self.drag_start_x: float = 0.0
        self.drag_start_y: float = 0.0
        self.selected_words: List[Tuple] = []  # List of word tuples
        self.selected_bounding_rect: Optional[fitz.Rect] = None

        # Drag Movement Engine State
        self.active_drag_type: Optional[str] = None  # "text" or "image"
        self.moving_item_data: Optional[Dict[str, Any]] = None

        # Page Added Images Registry: page_idx -> list of dicts {'rect': fitz.Rect, 'path': str, 'w': float, 'h': float}
        self.page_images_registry: Dict[int, List[Dict[str, Any]]] = {}

        # Canvas Image references
        self.canvas_photo_img = None
        self.thumbnail_images = []

        # Setup UI & Event Bindings
        self.setup_layout()
        self.bind_keyboard_events()

        # Load initial PDF if provided
        if initial_pdf and Path(initial_pdf).exists():
            self.load_pdf(initial_pdf)

    # -------------------------------------------------------------------------
    # LAYOUT SETUP
    # -------------------------------------------------------------------------
    def setup_layout(self):
        self.grid_rowconfigure(2, weight=1)
        self.grid_columnconfigure(1, weight=1)

        # 1. TOP TOOLBAR ROW 1 (Files, Page Nav, Zoom, Global Tools)
        self.toolbar_row1 = ctk.CTkFrame(self, corner_radius=0, height=45, fg_color=("gray85", "gray15"))
        self.toolbar_row1.grid(row=0, column=0, columnspan=2, sticky="ew")

        # File Operations
        btn_open = ctk.CTkButton(self.toolbar_row1, text="📂 Open PDF", width=95, command=self.action_open_file)
        btn_open.pack(side="left", padx=6, pady=6)

        btn_save = ctk.CTkButton(self.toolbar_row1, text="💾 Save As", width=90, fg_color="green", hover_color="darkgreen", command=self.action_save_as)
        btn_save.pack(side="left", padx=4, pady=6)

        # Separator
        ctk.CTkLabel(self.toolbar_row1, text="|", text_color="gray50").pack(side="left", padx=4)

        # Page Navigation
        self.btn_prev = ctk.CTkButton(self.toolbar_row1, text="◀", width=32, command=self.action_prev_page)
        self.btn_prev.pack(side="left", padx=2)

        self.lbl_page_num = ctk.CTkLabel(self.toolbar_row1, text="Page 0 of 0", font=ctk.CTkFont(weight="bold"))
        self.lbl_page_num.pack(side="left", padx=6)

        self.btn_next = ctk.CTkButton(self.toolbar_row1, text="▶", width=32, command=self.action_next_page)
        self.btn_next.pack(side="left", padx=2)

        # Separator
        ctk.CTkLabel(self.toolbar_row1, text="|", text_color="gray50").pack(side="left", padx=4)

        # Zoom Controls
        btn_zoom_out = ctk.CTkButton(self.toolbar_row1, text="🔍-", width=38, command=self.action_zoom_out)
        btn_zoom_out.pack(side="left", padx=2)

        self.lbl_zoom = ctk.CTkLabel(self.toolbar_row1, text="100%", width=45)
        self.lbl_zoom.pack(side="left", padx=2)

        btn_zoom_in = ctk.CTkButton(self.toolbar_row1, text="🔍+", width=38, command=self.action_zoom_in)
        btn_zoom_in.pack(side="left", padx=2)

        # Global Merge / Split Tools
        btn_merge = ctk.CTkButton(self.toolbar_row1, text="📑 Merge", width=75, fg_color="gray40", command=self.action_merge_dialog)
        btn_merge.pack(side="right", padx=6, pady=6)

        btn_split = ctk.CTkButton(self.toolbar_row1, text="✂️ Split", width=75, fg_color="gray40", command=self.action_split_dialog)
        btn_split.pack(side="right", padx=4, pady=6)

        # 2. TOP TOOLBAR ROW 2 (Modes, Font Controls, Add Image)
        self.toolbar_row2 = ctk.CTkFrame(self, corner_radius=0, height=45, fg_color=("gray90", "gray20"))
        self.toolbar_row2.grid(row=1, column=0, columnspan=2, sticky="ew")

        self.mode_var = ctk.StringVar(value="select_edit")

        rb_hl = ctk.CTkRadioButton(self.toolbar_row2, text="🖱️ Highlight / Move Text & Images", variable=self.mode_var, value="select_edit", command=self.on_mode_change)
        rb_hl.pack(side="left", padx=8, pady=6)

        rb_add = ctk.CTkRadioButton(self.toolbar_row2, text="➕ Add Text", variable=self.mode_var, value="add_text", command=self.on_mode_change)
        rb_add.pack(side="left", padx=8, pady=6)

        # Separator
        ctk.CTkLabel(self.toolbar_row2, text="|", text_color="gray50").pack(side="left", padx=6)

        # Font Controls
        ctk.CTkLabel(self.toolbar_row2, text="Font:").pack(side="left", padx=2)
        self.font_menu = ctk.CTkOptionMenu(
            self.toolbar_row2,
            values=["Helvetica", "Times-Roman", "Courier", "helv-bold"],
            width=110
        )
        self.font_menu.pack(side="left", padx=4)

        ctk.CTkLabel(self.toolbar_row2, text="Size:").pack(side="left", padx=2)
        self.font_size_menu = ctk.CTkOptionMenu(
            self.toolbar_row2,
            values=["8", "10", "12", "14", "16", "18", "20", "24", "32", "48", "72"],
            width=70
        )
        self.font_size_menu.set("12")
        self.font_size_menu.pack(side="left", padx=4)

        ctk.CTkLabel(self.toolbar_row2, text="Color:").pack(side="left", padx=2)
        self.color_menu = ctk.CTkOptionMenu(
            self.toolbar_row2,
            values=["Black", "Blue", "Red", "Green"],
            width=85
        )
        self.color_menu.pack(side="left", padx=4)

        # Separator
        ctk.CTkLabel(self.toolbar_row2, text="|", text_color="gray50").pack(side="left", padx=6)

        # Add Image Button
        btn_img = ctk.CTkButton(self.toolbar_row2, text="🖼️ Add Image", fg_color="darkblue", hover_color="navy", command=self.action_add_image)
        btn_img.pack(side="left", padx=6, pady=6)

        # 3. LEFT SIDEBAR - PAGE THUMBNAILS
        self.sidebar = ctk.CTkFrame(self, width=220, corner_radius=0)
        self.sidebar.grid(row=2, column=0, sticky="nsew", padx=0, pady=0)
        self.sidebar.grid_rowconfigure(1, weight=1)

        lbl_side = ctk.CTkLabel(self.sidebar, text="Page Thumbnails", font=ctk.CTkFont(size=14, weight="bold"))
        lbl_side.grid(row=0, column=0, padx=10, pady=10)

        self.thumb_scrollable = ctk.CTkScrollableFrame(self.sidebar, width=200)
        self.thumb_scrollable.grid(row=1, column=0, sticky="nsew", padx=5, pady=5)

        # 4. CENTER WORKSPACE - PAGE CANVAS
        self.center_frame = ctk.CTkFrame(self, fg_color=("gray90", "gray10"))
        self.center_frame.grid(row=2, column=1, sticky="nsew")
        self.center_frame.grid_rowconfigure(0, weight=1)
        self.center_frame.grid_columnconfigure(0, weight=1)

        # Scrollable Canvas Frame
        self.canvas_scroll = ctk.CTkScrollableFrame(self.center_frame, fg_color="transparent")
        self.canvas_scroll.grid(row=0, column=0, sticky="nsew", padx=5, pady=5)

        self.canvas = tk.Canvas(
            self.canvas_scroll,
            bg="#333333",
            highlightthickness=0,
            cursor="hand2"
        )
        self.canvas.pack(expand=True, fill="both", padx=10, pady=10)

        # Canvas Mouse Event Bindings
        self.canvas.bind("<ButtonPress-1>", self.on_canvas_press)
        self.canvas.bind("<B1-Motion>", self.on_canvas_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_canvas_release)

        # 5. BOTTOM STATUS BAR
        self.status_bar = ctk.CTkFrame(self, height=25, corner_radius=0)
        self.status_bar.grid(row=3, column=0, columnspan=2, sticky="ew")
        self.status_label = ctk.CTkLabel(self.status_bar, text="Open a PDF file to begin editing.", font=ctk.CTkFont(size=12))
        self.status_label.pack(side="left", padx=15, pady=2)

    def set_status(self, text: str, is_error: bool = False):
        color = ("red" if is_error else ("#1f538d", "#2fa572"))
        self.status_label.configure(text=text, text_color=color)

    def bind_keyboard_events(self):
        self.bind("<BackSpace>", self.on_key_backspace)
        self.bind("<Delete>", self.on_key_backspace)
        self.bind("<Key>", self.on_key_type)

    def on_mode_change(self):
        self.active_mode = self.mode_var.get()
        if self.active_mode == "select_edit":
            self.canvas.config(cursor="hand2")
            self.set_status("Mode: Drag to highlight text, or click & drag any text or image to reposition it!")
        elif self.active_mode == "add_text":
            self.canvas.config(cursor="cross")
            self.set_status("Mode: Click anywhere on the page canvas to drop and type new text.")

    # -------------------------------------------------------------------------
    # FILE LOADING & SAVING
    # -------------------------------------------------------------------------
    def action_open_file(self):
        f = filedialog.askopenfilename(filetypes=[("PDF Files", "*.pdf")])
        if f:
            self.load_pdf(f)

    def load_pdf(self, path: str):
        try:
            validate_pdf(path)
            if self.doc:
                self.doc.close()

            self.doc_path = path
            self.doc = fitz.open(path)
            self.current_page_idx = 0
            self.zoom_scale = 1.0

            self.selected_words.clear()
            self.selected_bounding_rect = None
            self.page_images_registry.clear()

            self.refresh_thumbnails()
            self.render_current_page()
            self.set_status(f"Loaded '{Path(path).name}' ({len(self.doc)} pages).")

        except Exception as e:
            messagebox.showerror("Error Opening PDF", str(e))
            self.set_status(f"Error loading PDF: {str(e)}", is_error=True)

    def action_save_as(self):
        if not self.doc:
            messagebox.showwarning("Warning", "No PDF file is currently open.")
            return

        out_path = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF Files", "*.pdf")])
        if out_path:
            try:
                self.doc.save(out_path)
                messagebox.showinfo("Saved", f"PDF successfully saved to:\n{out_path}")
                self.set_status(f"Saved document to '{out_path}'")
            except Exception as e:
                messagebox.showerror("Save Error", f"Failed to save PDF:\n{str(e)}")

    # -------------------------------------------------------------------------
    # THUMBNAILS SIDEBAR (WITH INSTANT DELETE & ROTATE)
    # -------------------------------------------------------------------------
    def refresh_thumbnails(self):
        for widget in self.thumb_scrollable.winfo_children():
            widget.destroy()

        self.thumbnail_images.clear()

        if not self.doc or len(self.doc) == 0:
            return

        for i in range(len(self.doc)):
            page = self.doc[i]
            pix = page.get_pixmap(dpi=40)
            img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            photo = ImageTk.PhotoImage(img)
            self.thumbnail_images.append(photo)

            card = ctk.CTkFrame(self.thumb_scrollable, fg_color=("gray80", "gray25") if i == self.current_page_idx else ("gray90", "gray18"))
            card.pack(fill="x", padx=5, pady=6)

            hdr = ctk.CTkFrame(card, fg_color="transparent")
            hdr.pack(fill="x", padx=4, pady=2)

            lbl_num = ctk.CTkLabel(hdr, text=f"Page {i+1}", font=ctk.CTkFont(size=12, weight="bold"))
            lbl_num.pack(side="left", padx=4)

            # 🗑️ DELETE PAGE BUTTON
            btn_del = ctk.CTkButton(
                hdr,
                text="🗑️ Delete",
                width=65,
                height=22,
                font=ctk.CTkFont(size=10, weight="bold"),
                fg_color="firebrick",
                hover_color="darkred",
                command=lambda p_idx=i: self.action_delete_page_thumbnail(p_idx)
            )
            btn_del.pack(side="right", padx=2)

            # 🔄 ROTATE PAGE BUTTON
            btn_rot = ctk.CTkButton(
                hdr,
                text="🔄",
                width=30,
                height=22,
                font=ctk.CTkFont(size=11),
                command=lambda p_idx=i: self.action_rotate_page_thumbnail(p_idx)
            )
            btn_rot.pack(side="right", padx=2)

            img_lbl = tk.Label(card, image=photo, bg="#222222", cursor="hand2")
            img_lbl.pack(padx=5, pady=4)
            img_lbl.bind("<Button-1>", lambda e, p_idx=i: self.jump_to_page(p_idx))

    def jump_to_page(self, page_idx: int):
        if self.doc and 0 <= page_idx < len(self.doc):
            self.current_page_idx = page_idx
            self.selected_words.clear()
            self.selected_bounding_rect = None
            self.refresh_thumbnails()
            self.render_current_page()

    def action_delete_page_thumbnail(self, page_idx: int):
        if not self.doc:
            return

        if len(self.doc) <= 1:
            messagebox.showwarning("Warning", "Cannot delete the only page in the PDF. At least 1 page must remain.")
            return

        confirm = messagebox.askyesno("Confirm Delete", f"Are you sure you want to delete Page {page_idx + 1}?")
        if confirm:
            self.doc.delete_page(page_idx)
            if self.current_page_idx >= len(self.doc):
                self.current_page_idx = max(0, len(self.doc) - 1)

            self.selected_words.clear()
            self.selected_bounding_rect = None
            self.refresh_thumbnails()
            self.render_current_page()
            self.set_status(f"Deleted Page {page_idx + 1}. Total pages remaining: {len(self.doc)}.")

    def action_rotate_page_thumbnail(self, page_idx: int):
        if not self.doc:
            return

        page = self.doc[page_idx]
        page.set_rotation((page.rotation + 90) % 360)

        self.refresh_thumbnails()
        self.render_current_page()
        self.set_status(f"Rotated Page {page_idx + 1} by 90°.")

    # -------------------------------------------------------------------------
    # INTERACTIVE PAGE CANVAS RENDERING
    # -------------------------------------------------------------------------
    def render_current_page(self):
        if not self.doc or len(self.doc) == 0:
            self.canvas.delete("all")
            self.lbl_page_num.configure(text="Page 0 of 0")
            return

        total = len(self.doc)
        self.current_page_idx = max(0, min(self.current_page_idx, total - 1))
        page = self.doc[self.current_page_idx]

        self.lbl_page_num.configure(text=f"Page {self.current_page_idx + 1} of {total}")
        self.lbl_zoom.configure(text=f"{int(self.zoom_scale * 100)}%")

        dpi = int(140 * self.zoom_scale)
        pix = page.get_pixmap(dpi=dpi)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        self.canvas_photo_img = ImageTk.PhotoImage(img)

        self.canvas.config(width=pix.width, height=pix.height)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self.canvas_photo_img)

        # Redraw highlight overlays & image borders
        self.redraw_text_selection_highlights()
        self.redraw_registered_image_overlays()

    def redraw_registered_image_overlays(self):
        dpi = int(140 * self.zoom_scale)
        scale = dpi / 72.0

        images = self.page_images_registry.get(self.current_page_idx, [])
        for img_item in images:
            r = img_item["rect"]
            x0, y0, x1, y1 = r.x0 * scale, r.y0 * scale, r.x1 * scale, r.y1 * scale
            self.canvas.create_rectangle(x0, y0, x1, y1, outline="#00bcd4", width=2, dash=(3, 2), tags="img_overlay")

    def action_prev_page(self):
        if self.doc and self.current_page_idx > 0:
            self.jump_to_page(self.current_page_idx - 1)

    def action_next_page(self):
        if self.doc and self.current_page_idx < len(self.doc) - 1:
            self.jump_to_page(self.current_page_idx + 1)

    def action_zoom_in(self):
        if self.zoom_scale < 2.5:
            self.zoom_scale += 0.25
            self.render_current_page()

    def action_zoom_out(self):
        if self.zoom_scale > 0.5:
            self.zoom_scale -= 0.25
            self.render_current_page()

    # -------------------------------------------------------------------------
    # UNIFIED CANVAS MOUSE DRAG HANDLERS (TEXT & IMAGES)
    # -------------------------------------------------------------------------
    def on_canvas_press(self, event):
        if not self.doc or len(self.doc) == 0:
            return

        self.drag_start_x = event.x
        self.drag_start_y = event.y
        self.active_drag_type = None
        self.moving_item_data = None

        dpi = int(140 * self.zoom_scale)
        scale = dpi / 72.0
        pdf_x, pdf_y = event.x / scale, event.y / scale
        click_point = fitz.Point(pdf_x, pdf_y)

        # Check 1: Did click hit an imported Image?
        images = self.page_images_registry.get(self.current_page_idx, [])
        for img_idx, img_item in enumerate(images):
            if img_item["rect"].contains(click_point):
                self.active_drag_type = "image"
                self.moving_item_data = {
                    "type": "image",
                    "img_idx": img_idx,
                    "img_item": img_item,
                    "offset_x": pdf_x - img_item["rect"].x0,
                    "offset_y": pdf_y - img_item["rect"].y0,
                    "w": img_item["rect"].width,
                    "h": img_item["rect"].height
                }
                self.set_status(f"Selected Image '{Path(img_item['path']).name}'. Drag mouse to reposition image!")
                return

        # Check 2: Did click hit an existing Text word / block to move?
        page = self.doc[self.current_page_idx]
        words = page.get_text("words")
        for w in words:
            r = fitz.Rect(w[:4])
            if r.contains(click_point):
                # We hit a text word!
                self.active_drag_type = "text_move_candidate"
                self.moving_item_data = {
                    "type": "text",
                    "word_tuple": w,
                    "text": w[4],
                    "orig_rect": r,
                    "offset_x": pdf_x - r.x0,
                    "offset_y": pdf_y - r.y0
                }
                break

        # Clear previous selection boxes
        self.selected_words.clear()
        self.selected_bounding_rect = None
        self.canvas.delete("text_hl")
        self.canvas.delete("drag_box")
        self.canvas.delete("drag_outline")

    def on_canvas_drag(self, event):
        if not self.doc or len(self.doc) == 0:
            return

        cur_x, cur_y = event.x, event.y

        # Mode A: Dragging an Image Element
        if self.active_drag_type == "image" and self.moving_item_data:
            self.canvas.delete("drag_outline")
            dpi = int(140 * self.zoom_scale)
            scale = dpi / 72.0

            w_px = self.moving_item_data["w"] * scale
            h_px = self.moving_item_data["h"] * scale
            x0 = cur_x - (self.moving_item_data["offset_x"] * scale)
            y0 = cur_y - (self.moving_item_data["offset_y"] * scale)

            self.canvas.create_rectangle(x0, y0, x0 + w_px, y0 + h_px, outline="#00bcd4", width=3, dash=(4, 2), tags="drag_outline")
            return

        # Mode B: Dragging a Text Element to move position
        if self.active_drag_type in ("text_move_candidate", "text_moving"):
            self.active_drag_type = "text_moving"
            self.canvas.delete("drag_outline")
            dpi = int(140 * self.zoom_scale)
            scale = dpi / 72.0

            r = self.moving_item_data["orig_rect"]
            w_px = r.width * scale
            h_px = r.height * scale
            x0 = cur_x - (self.moving_item_data["offset_x"] * scale)
            y0 = cur_y - (self.moving_item_data["offset_y"] * scale)

            self.canvas.create_rectangle(x0, y0, x0 + w_px, y0 + h_px, outline="#e91e63", width=2, dash=(3, 3), tags="drag_outline")
            return

        # Mode C: Dragging to Highlight Copy-Paste Text
        if self.active_mode == "select_edit":
            min_x, max_x = min(self.drag_start_x, cur_x), max(self.drag_start_x, cur_x)
            min_y, max_y = min(self.drag_start_y, cur_y), max(self.drag_start_y, cur_y)

            dpi = int(140 * self.zoom_scale)
            scale = dpi / 72.0
            selection_pdf_rect = fitz.Rect(min_x / scale, min_y / scale, max_x / scale, max_y / scale)

            page = self.doc[self.current_page_idx]
            words = page.get_text("words")

            selected_words = []
            for w in words:
                w_rect = fitz.Rect(w[:4])
                if selection_pdf_rect.intersects(w_rect):
                    selected_words.append(w)

            self.selected_words = selected_words
            self.redraw_text_selection_highlights()

    def on_canvas_release(self, event):
        if not self.doc or len(self.doc) == 0:
            return

        end_x, end_y = event.x, event.y
        dist = ((end_x - self.drag_start_x) ** 2 + (end_y - self.drag_start_y) ** 2) ** 0.5
        dpi = int(140 * self.zoom_scale)
        scale = dpi / 72.0

        # Release A: Finished Dragging Image to New Position
        if self.active_drag_type == "image" and self.moving_item_data and dist > 5.0:
            new_pdf_x = (end_x / scale) - self.moving_item_data["offset_x"]
            new_pdf_y = (end_y / scale) - self.moving_item_data["offset_y"]
            w = self.moving_item_data["w"]
            h = self.moving_item_data["h"]

            img_item = self.moving_item_data["img_item"]
            new_rect = fitz.Rect(new_pdf_x, new_pdf_y, new_pdf_x + w, new_pdf_y + h)
            img_item["rect"] = new_rect

            # Re-insert image into PDF page at new location
            page = self.doc[self.current_page_idx]
            page.insert_image(new_rect, filename=img_item["path"])

            self.active_drag_type = None
            self.moving_item_data = None
            self.canvas.delete("drag_outline")
            self.render_current_page()
            self.refresh_thumbnails()
            self.set_status(f"Repositioned image to ({int(new_pdf_x)}, {int(new_pdf_y)}).")
            return

        # Release B: Finished Dragging Text to New Position
        if self.active_drag_type == "text_moving" and self.moving_item_data and dist > 8.0:
            new_pdf_x = (end_x / scale) - self.moving_item_data["offset_x"]
            new_pdf_y = (end_y / scale) - self.moving_item_data["offset_y"]

            page = self.doc[self.current_page_idx]
            orig_rect = self.moving_item_data["orig_rect"]
            txt = self.moving_item_data["text"]

            page.add_redact_annot(orig_rect, fill=(1, 1, 1))
            page.apply_redactions()

            font_size = float(self.font_size_menu.get())
            font_name = self.font_menu.get().lower()
            if "helv" in font_name:
                font_name = "helv"

            page.insert_text(fitz.Point(new_pdf_x, new_pdf_y + orig_rect.height), txt, fontsize=font_size, fontname=font_name, color=(0, 0, 0))

            self.active_drag_type = None
            self.moving_item_data = None
            self.canvas.delete("drag_outline")
            self.render_current_page()
            self.refresh_thumbnails()
            self.set_status(f"Moved text '{txt}' to ({int(new_pdf_x)}, {int(new_pdf_y)}).")
            return

        # Release C: Finalize Text Selection Highlighting
        if self.active_mode == "select_edit":
            if self.selected_words:
                x0 = min(w[0] for w in self.selected_words)
                y0 = min(w[1] for w in self.selected_words)
                x1 = max(w[2] for w in self.selected_words)
                y1 = max(w[3] for w in self.selected_words)
                self.selected_bounding_rect = fitz.Rect(x0, y0, x1, y1)

                sel_text = " ".join(w[4] for w in self.selected_words)
                self.set_status(f"Highlighted text: '{sel_text}'. Press BACKSPACE to erase or START TYPING to replace!")

        elif self.active_mode == "add_text" and dist < 10.0:
            self.handle_click_add_text(end_x / scale, end_y / scale)

    def redraw_text_selection_highlights(self):
        self.canvas.delete("text_hl")
        if not self.selected_words:
            return

        dpi = int(140 * self.zoom_scale)
        scale = dpi / 72.0

        for w in self.selected_words:
            w_rect = fitz.Rect(w[:4])
            x0 = w_rect.x0 * scale
            y0 = w_rect.y0 * scale
            x1 = w_rect.x1 * scale
            y1 = w_rect.y1 * scale

            self.canvas.create_rectangle(
                x0, y0, x1, y1,
                fill="#ffeb3b",
                outline="#fbc02d",
                width=1,
                tags="text_hl"
            )

    # -------------------------------------------------------------------------
    # DIRECT KEYBOARD TYPING & BACKSPACE REPLACEMENT
    # -------------------------------------------------------------------------
    def on_key_backspace(self, event):
        if not self.doc or not self.selected_words or not self.selected_bounding_rect:
            return

        page = self.doc[self.current_page_idx]
        page.add_redact_annot(self.selected_bounding_rect, fill=(1, 1, 1))
        page.apply_redactions()

        sel_text = " ".join(w[4] for w in self.selected_words)
        self.selected_words.clear()
        self.selected_bounding_rect = None

        self.render_current_page()
        self.refresh_thumbnails()
        self.set_status(f"Erased highlighted text: '{sel_text}'.")

    def on_key_type(self, event):
        if not event.char or ord(event.char) < 32 or event.keysym in ("BackSpace", "Delete", "Return", "Escape", "Tab"):
            return

        if not self.doc or not self.selected_words or not self.selected_bounding_rect:
            return

        typed_char = event.char
        orig_text = " ".join(w[4] for w in self.selected_words)
        rect = self.selected_bounding_rect

        self.open_inline_direct_typing_dialog(orig_text, typed_char, rect)

    def open_inline_direct_typing_dialog(self, original_text: str, initial_typed: str, rect: fitz.Rect):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Type Replacement Text")
        dialog.geometry("450x260")
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text=f"Replacing: \"{original_text}\"", font=ctk.CTkFont(size=12), text_color="gray70").pack(anchor="w", padx=15, pady=(12, 2))
        ctk.CTkLabel(dialog, text="Type Replacement Content:", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=15, pady=2)

        entry = ctk.CTkEntry(dialog, font=ctk.CTkFont(size=14))
        entry.insert(0, initial_typed)
        entry.pack(fill="x", padx=15, pady=8)
        entry.focus()
        entry.icursor(tk.END)

        def apply_typing():
            new_text = entry.get()
            dialog.destroy()

            page = self.doc[self.current_page_idx]
            page.add_redact_annot(rect, fill=(1, 1, 1))
            page.apply_redactions()

            font_size = float(self.font_size_menu.get())
            font_name = self.font_menu.get().lower()
            if "helv" in font_name:
                font_name = "helv"

            color_choice = self.color_menu.get()
            color_map = {
                "Black": (0, 0, 0),
                "Blue": (0, 0.2, 0.8),
                "Red": (0.8, 0.1, 0.1),
                "Green": (0.1, 0.6, 0.2)
            }
            color_rgb = color_map.get(color_choice, (0, 0, 0))

            if new_text:
                baseline_point = fitz.Point(rect.x0, rect.y1 - (rect.height * 0.15))
                page.insert_text(baseline_point, new_text, fontsize=font_size, fontname=font_name, color=color_rgb)

            self.selected_words.clear()
            self.selected_bounding_rect = None
            self.render_current_page()
            self.refresh_thumbnails()
            self.set_status(f"Replaced text: '{original_text}' -> '{new_text}'")

        btn_save = ctk.CTkButton(dialog, text="Apply Replacement (Enter)", fg_color="green", hover_color="darkgreen", command=apply_typing)
        btn_save.pack(pady=15)
        dialog.bind("<Return>", lambda e: apply_typing())

    def handle_click_add_text(self, pdf_x: float, pdf_y: float):
        dialog = ctk.CTkToplevel(self)
        dialog.title("Add Text Overlay")
        dialog.geometry("380x200")
        dialog.transient(self)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text=f"Type text to insert at ({int(pdf_x)}, {int(pdf_y)}):", font=ctk.CTkFont(weight="bold")).pack(anchor="w", padx=15, pady=(15, 5))

        entry_text = ctk.CTkEntry(dialog, font=ctk.CTkFont(size=14))
        entry_text.pack(fill="x", padx=15, pady=10)
        entry_text.focus()

        def apply_add():
            new_val = entry_text.get().strip()
            if not new_val:
                dialog.destroy()
                return

            dialog.destroy()
            page = self.doc[self.current_page_idx]
            point = fitz.Point(pdf_x, pdf_y)

            font_size = float(self.font_size_menu.get())
            font_name = self.font_menu.get().lower()
            if "helv" in font_name:
                font_name = "helv"

            page.insert_text(point, new_val, fontsize=font_size, fontname=font_name, color=(0, 0, 0))

            self.render_current_page()
            self.refresh_thumbnails()
            self.set_status(f"Added text overlay at ({int(pdf_x)}, {int(pdf_y)})")

        btn_save = ctk.CTkButton(dialog, text="Insert Text", fg_color="green", hover_color="darkgreen", command=apply_add)
        btn_save.pack(pady=15)

    # -------------------------------------------------------------------------
    # ADD & REPOSITION IMAGE ON PDF
    # -------------------------------------------------------------------------
    def action_add_image(self):
        if not self.doc or len(self.doc) == 0:
            messagebox.showwarning("Warning", "Please open a PDF file first.")
            return

        img_file = filedialog.askopenfilename(
            filetypes=[("Image Files", "*.png *.jpg *.jpeg *.bmp *.webp")]
        )
        if not img_file:
            return

        page = self.doc[self.current_page_idx]
        page_rect = page.rect

        w, h = 180.0, 180.0
        x0 = max(20.0, (page_rect.width - w) / 2.0)
        y0 = max(20.0, (page_rect.height - h) / 2.0)
        img_rect = fitz.Rect(x0, y0, x0 + w, y0 + h)

        # Insert image into PDF page
        page.insert_image(img_rect, filename=img_file)

        # Register in page_images_registry for canvas drag repositioning
        if self.current_page_idx not in self.page_images_registry:
            self.page_images_registry[self.current_page_idx] = []

        self.page_images_registry[self.current_page_idx].append({
            "rect": img_rect,
            "path": img_file,
            "w": w,
            "h": h
        })

        self.render_current_page()
        self.refresh_thumbnails()
        self.set_status(f"Inserted image '{Path(img_file).name}' onto Page {self.current_page_idx + 1}. Drag image to reposition!")

    # -------------------------------------------------------------------------
    # MERGE & SPLIT DIALOGS
    # -------------------------------------------------------------------------
    def action_merge_dialog(self):
        files = filedialog.askopenfilenames(filetypes=[("PDF Files", "*.pdf")])
        if len(files) < 2:
            messagebox.showwarning("Warning", "Please select at least 2 PDF files to merge.")
            return

        out_f = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF Files", "*.pdf")])
        if out_f:
            try:
                dest = merge_pdfs(files, out_f)
                messagebox.showinfo("Success", f"Merged {len(files)} files successfully!\nSaved to: {dest}")
                self.load_pdf(str(dest))
            except Exception as e:
                messagebox.showerror("Merge Error", str(e))

    def action_split_dialog(self):
        if not self.doc_path:
            messagebox.showwarning("Warning", "Please open a PDF document first.")
            return

        out_dir = filedialog.askdirectory()
        if out_dir:
            try:
                created = split_to_individual_pages(self.doc_path, out_dir)
                messagebox.showinfo("Success", f"Split PDF into {len(created)} pages in:\n{out_dir}")
            except Exception as e:
                messagebox.showerror("Split Error", str(e))


def launch_adobe_gui():
    sample_path = "samples/sample_report.pdf" if Path("samples/sample_report.pdf").exists() else None
    app = AdobePDFEditorApp(initial_pdf=sample_path)
    app.mainloop()


if __name__ == "__main__":
    launch_adobe_gui()
