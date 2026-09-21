"""
Graphical User Interface (GUI) Application for PDF Editor using CustomTkinter.
"""

import sys
import os
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox
import customtkinter as ctk
import pymupdf as fitz

from pdf_editor.core.utils import validate_pdf, PDFEditorError
from pdf_editor.core.merger import merge_pdfs
from pdf_editor.core.splitter import split_by_ranges, split_by_bookmarks, split_to_individual_pages
from pdf_editor.core.editor import rotate_pages, reorder_pages, delete_pages, extract_pages, replace_pages
from pdf_editor.core.text_editor import replace_text, add_text, redact_text, search_text

# Set theme
ctk.set_appearance_mode("System")
ctk.set_default_color_theme("blue")


class PDFEditorApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title("PDF Editor Application")
        self.geometry("1100x750")
        self.minsize(950, 650)

        # Main Layout: Top Header, Center Tabview, Bottom Status Bar
        self.grid_rowconfigure(1, weight=1)
        self.grid_columnconfigure(0, weight=1)

        # Header Frame
        self.header_frame = ctk.CTkFrame(self, corner_radius=0, fg_color=("gray85", "gray15"))
        self.header_frame.grid(row=0, column=0, sticky="ew", padx=0, pady=0)

        self.header_title = ctk.CTkLabel(
            self.header_frame,
            text="📄 Professional PDF Editor Toolkit",
            font=ctk.CTkFont(size=20, weight="bold")
        )
        self.header_title.pack(side="left", padx=20, pady=12)

        self.theme_menu = ctk.CTkOptionMenu(
            self.header_frame,
            values=["System", "Dark", "Light"],
            command=self.change_theme,
            width=110
        )
        self.theme_menu.pack(side="right", padx=20, pady=12)
        self.theme_menu.set("System")

        # Tabview Container
        self.tabview = ctk.CTkTabview(self, corner_radius=8)
        self.tabview.grid(row=1, column=0, sticky="nsew", padx=15, pady=10)

        self.tab_info = self.tabview.add(" Document Info ")
        self.tab_merge = self.tabview.add(" Merge PDFs ")
        self.tab_split = self.tabview.add(" Split PDF ")
        self.tab_page = self.tabview.add(" Page Editor ")
        self.tab_text = self.tabview.add(" Text Editor ")

        # Status Bar
        self.status_frame = ctk.CTkFrame(self, height=30, corner_radius=0)
        self.status_frame.grid(row=2, column=0, sticky="ew")
        self.status_label = ctk.CTkLabel(self.status_frame, text="Ready", font=ctk.CTkFont(size=12))
        self.status_label.pack(side="left", padx=15, pady=4)

        # Initialize Tabs
        self.setup_info_tab()
        self.setup_merge_tab()
        self.setup_split_tab()
        self.setup_page_tab()
        self.setup_text_tab()

    def set_status(self, text: str, is_error: bool = False):
        color = ("red" if is_error else ("#1f538d", "#2fa572"))
        self.status_label.configure(text=text, text_color=color)

    def change_theme(self, new_theme: str):
        ctk.set_appearance_mode(new_theme)

    # -------------------------------------------------------------------------
    # TAB 1: Document Info & Bookmarks
    # -------------------------------------------------------------------------
    def setup_info_tab(self):
        frame = self.tab_info
        frame.grid_columnconfigure(1, weight=1)

        # File Select Header
        lbl = ctk.CTkLabel(frame, text="Select PDF File to Inspect:", font=ctk.CTkFont(size=14, weight="bold"))
        lbl.grid(row=0, column=0, sticky="w", padx=10, pady=(15, 5))

        self.info_file_entry = ctk.CTkEntry(frame, placeholder_text="Path to PDF file...")
        self.info_file_entry.grid(row=0, column=1, sticky="ew", padx=10, pady=(15, 5))

        btn_browse = ctk.CTkButton(frame, text="Browse...", width=100, command=self.browse_info_file)
        btn_browse.grid(row=0, column=2, padx=10, pady=(15, 5))

        btn_inspect = ctk.CTkButton(frame, text="Inspect PDF", width=120, command=self.inspect_pdf)
        btn_inspect.grid(row=0, column=3, padx=10, pady=(15, 5))

        # Metadata Card
        self.info_meta_textbox = ctk.CTkTextbox(frame, height=180, font=ctk.CTkFont(family="Consolas", size=13))
        self.info_meta_textbox.grid(row=1, column=0, columnspan=4, sticky="ew", padx=10, pady=10)

        # Outline Bookmarks Header
        lbl_bm = ctk.CTkLabel(frame, text="Bookmarks & Table of Contents Outline:", font=ctk.CTkFont(size=14, weight="bold"))
        lbl_bm.grid(row=2, column=0, sticky="w", padx=10, pady=(10, 5))

        self.info_toc_textbox = ctk.CTkTextbox(frame, font=ctk.CTkFont(family="Consolas", size=13))
        self.info_toc_textbox.grid(row=3, column=0, columnspan=4, sticky="nsew", padx=10, pady=(5, 10))
        frame.grid_rowconfigure(3, weight=1)

    def browse_info_file(self):
        f = filedialog.askopenfilename(filetypes=[("PDF Files", "*.pdf")])
        if f:
            self.info_file_entry.delete(0, tk.END)
            self.info_file_entry.insert(0, f)
            self.inspect_pdf()

    def inspect_pdf(self):
        path_str = self.info_file_entry.get().strip()
        if not path_str:
            messagebox.showwarning("Warning", "Please select a PDF file first.")
            return

        try:
            total_pages = validate_pdf(path_str)
            doc = fitz.open(path_str)
            meta = doc.metadata
            toc = doc.get_toc()
            doc.close()

            self.info_meta_textbox.delete("1.0", tk.END)
            meta_text = (
                f"File Path: {path_str}\n"
                f"Total Pages: {total_pages}\n"
                f"Title: {meta.get('title', 'N/A')}\n"
                f"Author: {meta.get('author', 'N/A')}\n"
                f"Subject: {meta.get('subject', 'N/A')}\n"
                f"Producer: {meta.get('producer', 'N/A')}\n"
                f"Creation Date: {meta.get('creationDate', 'N/A')}\n"
                f"Encrypted: No\n"
            )
            self.info_meta_textbox.insert("1.0", meta_text)

            self.info_toc_textbox.delete("1.0", tk.END)
            if not toc:
                self.info_toc_textbox.insert("1.0", "No bookmarks or outline entries found in document.")
            else:
                toc_text = "Level | Title                                      | Target Page\n"
                toc_text += "-" * 70 + "\n"
                for lvl, title, page in toc:
                    indent = "  " * (lvl - 1)
                    toc_text += f"Lvl {lvl:02d} | {indent}{title:<40} | Page {page}\n"
                self.info_toc_textbox.insert("1.0", toc_text)

            self.set_status(f"Inspected '{Path(path_str).name}' - {total_pages} pages.")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to inspect PDF:\n{str(e)}")
            self.set_status(f"Error inspecting PDF: {str(e)}", is_error=True)

    # -------------------------------------------------------------------------
    # TAB 2: Merge PDFs
    # -------------------------------------------------------------------------
    def setup_merge_tab(self):
        frame = self.tab_merge
        frame.grid_columnconfigure(0, weight=1)
        frame.grid_rowconfigure(1, weight=1)

        # Top Button controls
        ctrl_frame = ctk.CTkFrame(frame)
        ctrl_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=10)

        btn_add = ctk.CTkButton(ctrl_frame, text="➕ Add PDF Files", command=self.merge_add_files)
        btn_add.pack(side="left", padx=5, pady=8)

        btn_remove = ctk.CTkButton(ctrl_frame, text="❌ Remove Selected", fg_color="firebrick", hover_color="darkred", command=self.merge_remove_file)
        btn_remove.pack(side="left", padx=5, pady=8)

        btn_up = ctk.CTkButton(ctrl_frame, text="⬆ Move Up", width=90, command=self.merge_move_up)
        btn_up.pack(side="left", padx=5, pady=8)

        btn_down = ctk.CTkButton(ctrl_frame, text="⬇ Move Down", width=90, command=self.merge_move_down)
        btn_down.pack(side="left", padx=5, pady=8)

        btn_clear = ctk.CTkButton(ctrl_frame, text="Clear List", width=80, fg_color="gray50", command=self.merge_clear)
        btn_clear.pack(side="right", padx=5, pady=8)

        # Listbox Container
        self.merge_listbox = tk.Listbox(
            frame,
            selectmode=tk.SINGLE,
            font=("Consolas", 11),
            bg="#2b2b2b",
            fg="white",
            selectbackground="#1f538d",
            activestyle="none"
        )
        self.merge_listbox.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)

        # Bottom Output Controls
        out_frame = ctk.CTkFrame(frame)
        out_frame.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
        out_frame.grid_columnconfigure(1, weight=1)

        self.merge_bm_cb = ctk.CTkCheckBox(out_frame, text="Generate Outline Bookmarks per Source PDF")
        self.merge_bm_cb.select()
        self.merge_bm_cb.grid(row=0, column=0, columnspan=3, sticky="w", padx=10, pady=5)

        lbl_out = ctk.CTkLabel(out_frame, text="Output Path:", font=ctk.CTkFont(weight="bold"))
        lbl_out.grid(row=1, column=0, padx=10, pady=5)

        self.merge_out_entry = ctk.CTkEntry(out_frame, placeholder_text="Choose destination merged file...")
        self.merge_out_entry.grid(row=1, column=1, sticky="ew", padx=10, pady=5)

        btn_out_browse = ctk.CTkButton(out_frame, text="Browse Output", width=120, command=self.merge_browse_out)
        btn_out_browse.grid(row=1, column=2, padx=10, pady=5)

        btn_execute = ctk.CTkButton(out_frame, text="🚀 Merge PDFs Now", font=ctk.CTkFont(size=14, weight="bold"), height=40, fg_color="green", hover_color="darkgreen", command=self.merge_execute)
        btn_execute.grid(row=2, column=0, columnspan=3, sticky="ew", padx=10, pady=10)

    def merge_add_files(self):
        files = filedialog.askopenfilenames(filetypes=[("PDF Files", "*.pdf")])
        for f in files:
            self.merge_listbox.insert(tk.END, f)

    def merge_remove_file(self):
        sel = self.merge_listbox.curselection()
        if sel:
            self.merge_listbox.delete(sel[0])

    def merge_clear(self):
        self.merge_listbox.delete(0, tk.END)

    def merge_move_up(self):
        sel = self.merge_listbox.curselection()
        if sel and sel[0] > 0:
            idx = sel[0]
            val = self.merge_listbox.get(idx)
            self.merge_listbox.delete(idx)
            self.merge_listbox.insert(idx - 1, val)
            self.merge_listbox.select_set(idx - 1)

    def merge_move_down(self):
        sel = self.merge_listbox.curselection()
        if sel and sel[0] < self.merge_listbox.size() - 1:
            idx = sel[0]
            val = self.merge_listbox.get(idx)
            self.merge_listbox.delete(idx)
            self.merge_listbox.insert(idx + 1, val)
            self.merge_listbox.select_set(idx + 1)

    def merge_browse_out(self):
        f = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF Files", "*.pdf")])
        if f:
            self.merge_out_entry.delete(0, tk.END)
            self.merge_out_entry.insert(0, f)

    def merge_execute(self):
        items = list(self.merge_listbox.get(0, tk.END))
        out_path = self.merge_out_entry.get().strip()

        if len(items) < 2:
            messagebox.showwarning("Warning", "Please add at least 2 PDF files to merge.")
            return

        if not out_path:
            messagebox.showwarning("Warning", "Please choose an output destination file.")
            return

        try:
            dest = merge_pdfs(items, out_path, add_bookmarks=bool(self.merge_bm_cb.get()))
            messagebox.showinfo("Success", f"Merged {len(items)} PDFs successfully!\nSaved to: {dest}")
            self.set_status(f"Successfully merged {len(items)} files into '{dest}'")
        except Exception as e:
            messagebox.showerror("Error", f"Failed to merge PDFs:\n{str(e)}")
            self.set_status(f"Merge error: {str(e)}", is_error=True)

    # -------------------------------------------------------------------------
    # TAB 3: Split PDF
    # -------------------------------------------------------------------------
    def setup_split_tab(self):
        frame = self.tab_split
        frame.grid_columnconfigure(1, weight=1)

        # File Select
        lbl_in = ctk.CTkLabel(frame, text="Input PDF File:", font=ctk.CTkFont(weight="bold"))
        lbl_in.grid(row=0, column=0, sticky="w", padx=15, pady=(20, 10))

        self.split_in_entry = ctk.CTkEntry(frame, placeholder_text="Select input PDF file...")
        self.split_in_entry.grid(row=0, column=1, sticky="ew", padx=10, pady=(20, 10))

        btn_browse_in = ctk.CTkButton(frame, text="Browse...", width=100, command=self.split_browse_in)
        btn_browse_in.grid(row=0, column=2, padx=15, pady=(20, 10))

        # Split Options Group
        group = ctk.CTkFrame(frame)
        group.grid(row=1, column=0, columnspan=3, sticky="ew", padx=15, pady=15)
        group.grid_columnconfigure(1, weight=1)

        self.split_mode_var = ctk.StringVar(value="ranges")

        rb_ranges = ctk.CTkRadioButton(group, text="Split by Custom Page Ranges", variable=self.split_mode_var, value="ranges")
        rb_ranges.grid(row=0, column=0, sticky="w", padx=15, pady=10)

        self.split_ranges_entry = ctk.CTkEntry(group, placeholder_text="e.g. 1-3, 4-6, 7-end")
        self.split_ranges_entry.grid(row=0, column=1, sticky="ew", padx=15, pady=10)
        self.split_ranges_entry.insert(0, "1-2, 3-end")

        rb_bookmarks = ctk.CTkRadioButton(group, text="Split by Outline Bookmarks / Sections", variable=self.split_mode_var, value="bookmarks")
        rb_bookmarks.grid(row=1, column=0, columnspan=2, sticky="w", padx=15, pady=10)

        rb_all = ctk.CTkRadioButton(group, text="Split Every Page into Individual PDF File", variable=self.split_mode_var, value="all")
        rb_all.grid(row=2, column=0, columnspan=2, sticky="w", padx=15, pady=10)

        # Destination Folder
        lbl_out = ctk.CTkLabel(frame, text="Output Directory:", font=ctk.CTkFont(weight="bold"))
        lbl_out.grid(row=2, column=0, sticky="w", padx=15, pady=10)

        self.split_out_entry = ctk.CTkEntry(frame, placeholder_text="Select output directory...")
        self.split_out_entry.grid(row=2, column=1, sticky="ew", padx=10, pady=10)

        btn_browse_out = ctk.CTkButton(frame, text="Browse Dir...", width=100, command=self.split_browse_out)
        btn_browse_out.grid(row=2, column=2, padx=15, pady=10)

        btn_execute = ctk.CTkButton(frame, text="✂️ Split PDF Now", font=ctk.CTkFont(size=14, weight="bold"), height=40, fg_color="green", hover_color="darkgreen", command=self.split_execute)
        btn_execute.grid(row=3, column=0, columnspan=3, sticky="ew", padx=15, pady=20)

    def split_browse_in(self):
        f = filedialog.askopenfilename(filetypes=[("PDF Files", "*.pdf")])
        if f:
            self.split_in_entry.delete(0, tk.END)
            self.split_in_entry.insert(0, f)

    def split_browse_out(self):
        d = filedialog.askdirectory()
        if d:
            self.split_out_entry.delete(0, tk.END)
            self.split_out_entry.insert(0, d)

    def split_execute(self):
        in_path = self.split_in_entry.get().strip()
        out_dir = self.split_out_entry.get().strip()
        mode = self.split_mode_var.get()

        if not in_path or not out_dir:
            messagebox.showwarning("Warning", "Please select input file and output directory.")
            return

        try:
            if mode == "ranges":
                r_spec = self.split_ranges_entry.get().strip()
                ranges_list = [r.strip() for r in r_spec.split(",") if r.strip()]
                files = split_by_ranges(in_path, ranges_list, out_dir)
            elif mode == "bookmarks":
                files = split_by_bookmarks(in_path, out_dir)
            elif mode == "all":
                files = split_to_individual_pages(in_path, out_dir)

            messagebox.showinfo("Success", f"Split operation complete!\nCreated {len(files)} files in '{out_dir}'")
            self.set_status(f"Split PDF into {len(files)} files in '{out_dir}'")

        except Exception as e:
            messagebox.showerror("Error", f"Failed to split PDF:\n{str(e)}")
            self.set_status(f"Split error: {str(e)}", is_error=True)

    # -------------------------------------------------------------------------
    # TAB 4: Page Editor
    # -------------------------------------------------------------------------
    def setup_page_tab(self):
        frame = self.tab_page
        frame.grid_columnconfigure(1, weight=1)

        # Input / Output File Pickers
        lbl_in = ctk.CTkLabel(frame, text="Input PDF:", font=ctk.CTkFont(weight="bold"))
        lbl_in.grid(row=0, column=0, sticky="w", padx=15, pady=(15, 5))
        self.page_in_entry = ctk.CTkEntry(frame)
        self.page_in_entry.grid(row=0, column=1, sticky="ew", padx=10, pady=(15, 5))
        btn_in = ctk.CTkButton(frame, text="Browse...", width=90, command=lambda: self.browse_file_to_entry(self.page_in_entry))
        btn_in.grid(row=0, column=2, padx=15, pady=(15, 5))

        lbl_out = ctk.CTkLabel(frame, text="Output PDF:", font=ctk.CTkFont(weight="bold"))
        lbl_out.grid(row=1, column=0, sticky="w", padx=15, pady=5)
        self.page_out_entry = ctk.CTkEntry(frame)
        self.page_out_entry.grid(row=1, column=1, sticky="ew", padx=10, pady=5)
        btn_out = ctk.CTkButton(frame, text="Browse...", width=90, command=lambda: self.browse_save_to_entry(self.page_out_entry))
        btn_out.grid(row=1, column=2, padx=15, pady=5)

        # Tabbed Sub-Operations Frame
        op_tab = ctk.CTkTabview(frame)
        op_tab.grid(row=2, column=0, columnspan=3, sticky="nsew", padx=15, pady=10)
        frame.grid_rowconfigure(2, weight=1)

        sub_rot = op_tab.add("Rotate")
        sub_reorder = op_tab.add("Reorder")
        sub_del = op_tab.add("Delete Pages")
        sub_ext = op_tab.add("Extract Pages")
        sub_repl = op_tab.add("Replace Pages")

        # Sub-tab: Rotate
        ctk.CTkLabel(sub_rot, text="Target Pages (e.g. '1,3' or 'all'):").pack(anchor="w", padx=15, pady=(15, 2))
        self.rot_pages_entry = ctk.CTkEntry(sub_rot)
        self.rot_pages_entry.insert(0, "all")
        self.rot_pages_entry.pack(fill="x", padx=15, pady=5)

        ctk.CTkLabel(sub_rot, text="Rotation Angle:").pack(anchor="w", padx=15, pady=(10, 2))
        self.rot_angle_menu = ctk.CTkOptionMenu(sub_rot, values=["90", "180", "270"])
        self.rot_angle_menu.pack(anchor="w", padx=15, pady=5)

        btn_do_rot = ctk.CTkButton(sub_rot, text="Apply Rotation", fg_color="green", hover_color="darkgreen", command=self.page_do_rotate)
        btn_do_rot.pack(padx=15, pady=20)

        # Sub-tab: Reorder
        ctk.CTkLabel(sub_reorder, text="New 1-based Page Sequence (e.g. '3,1,2,4'):").pack(anchor="w", padx=15, pady=(15, 2))
        self.reorder_entry = ctk.CTkEntry(sub_reorder)
        self.reorder_entry.pack(fill="x", padx=15, pady=5)
        btn_do_reorder = ctk.CTkButton(sub_reorder, text="Apply Reorder", fg_color="green", hover_color="darkgreen", command=self.page_do_reorder)
        btn_do_reorder.pack(padx=15, pady=20)

        # Sub-tab: Delete
        ctk.CTkLabel(sub_del, text="Pages to Delete (e.g. '2, 4-6'):").pack(anchor="w", padx=15, pady=(15, 2))
        self.del_pages_entry = ctk.CTkEntry(sub_del)
        self.del_pages_entry.pack(fill="x", padx=15, pady=5)
        btn_do_del = ctk.CTkButton(sub_del, text="Delete Pages", fg_color="firebrick", hover_color="darkred", command=self.page_do_delete)
        btn_do_del.pack(padx=15, pady=20)

        # Sub-tab: Extract
        ctk.CTkLabel(sub_ext, text="Pages to Extract (e.g. '1-3, 5'):").pack(anchor="w", padx=15, pady=(15, 2))
        self.ext_pages_entry = ctk.CTkEntry(sub_ext)
        self.ext_pages_entry.pack(fill="x", padx=15, pady=5)
        btn_do_ext = ctk.CTkButton(sub_ext, text="Extract Pages", fg_color="green", hover_color="darkgreen", command=self.page_do_extract)
        btn_do_ext.pack(padx=15, pady=20)

        # Sub-tab: Replace Pages
        ctk.CTkLabel(sub_repl, text="Target Pages in Main PDF to Replace (e.g. '2'):").pack(anchor="w", padx=15, pady=(10, 2))
        self.repl_target_entry = ctk.CTkEntry(sub_repl)
        self.repl_target_entry.pack(fill="x", padx=15, pady=2)

        ctk.CTkLabel(sub_repl, text="Replacement Source PDF File:").pack(anchor="w", padx=15, pady=(10, 2))
        repl_file_frame = ctk.CTkFrame(sub_repl, fg_color="transparent")
        repl_file_frame.pack(fill="x", padx=15, pady=2)
        self.repl_src_entry = ctk.CTkEntry(repl_file_frame)
        self.repl_src_entry.pack(side="left", fill="x", expand=True, padx=(0, 5))
        ctk.CTkButton(repl_file_frame, text="Browse...", width=80, command=lambda: self.browse_file_to_entry(self.repl_src_entry)).pack(side="right")

        btn_do_repl = ctk.CTkButton(sub_repl, text="Replace Pages", fg_color="green", hover_color="darkgreen", command=self.page_do_replace)
        btn_do_repl.pack(padx=15, pady=15)

    def browse_file_to_entry(self, entry_widget):
        f = filedialog.askopenfilename(filetypes=[("PDF Files", "*.pdf")])
        if f:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, f)

    def browse_save_to_entry(self, entry_widget):
        f = filedialog.asksaveasfilename(defaultextension=".pdf", filetypes=[("PDF Files", "*.pdf")])
        if f:
            entry_widget.delete(0, tk.END)
            entry_widget.insert(0, f)

    def page_do_rotate(self):
        inp, out = self.page_in_entry.get().strip(), self.page_out_entry.get().strip()
        if not inp or not out:
            messagebox.showwarning("Warning", "Please select input and output PDF files.")
            return
        try:
            angle = int(self.rot_angle_menu.get())
            pages = self.rot_pages_entry.get().strip()
            dest = rotate_pages(inp, out, page_angles=angle, target_pages=pages)
            messagebox.showinfo("Success", f"Rotated pages by {angle}° successfully!\nSaved to: {dest}")
            self.set_status(f"Rotated pages saved to '{dest}'")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def page_do_reorder(self):
        inp, out = self.page_in_entry.get().strip(), self.page_out_entry.get().strip()
        if not inp or not out:
            messagebox.showwarning("Warning", "Please select input and output PDF files.")
            return
        try:
            order_str = self.reorder_entry.get().strip()
            order_list = [int(p.strip()) for p in order_str.split(",") if p.strip()]
            dest = reorder_pages(inp, out, order_list)
            messagebox.showinfo("Success", f"Reordered pages saved to:\n{dest}")
            self.set_status(f"Reordered pages saved to '{dest}'")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def page_do_delete(self):
        inp, out = self.page_in_entry.get().strip(), self.page_out_entry.get().strip()
        if not inp or not out:
            messagebox.showwarning("Warning", "Please select input and output PDF files.")
            return
        try:
            pages = self.del_pages_entry.get().strip()
            dest = delete_pages(inp, out, pages)
            messagebox.showinfo("Success", f"Deleted pages ({pages}) successfully!\nSaved to: {dest}")
            self.set_status(f"Deleted pages saved to '{dest}'")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def page_do_extract(self):
        inp, out = self.page_in_entry.get().strip(), self.page_out_entry.get().strip()
        if not inp or not out:
            messagebox.showwarning("Warning", "Please select input and output PDF files.")
            return
        try:
            pages = self.ext_pages_entry.get().strip()
            dest = extract_pages(inp, out, pages)
            messagebox.showinfo("Success", f"Extracted pages ({pages}) saved to:\n{dest}")
            self.set_status(f"Extracted pages saved to '{dest}'")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def page_do_replace(self):
        inp, out = self.page_in_entry.get().strip(), self.page_out_entry.get().strip()
        src = self.repl_src_entry.get().strip()
        target = self.repl_target_entry.get().strip()
        if not inp or not out or not src or not target:
            messagebox.showwarning("Warning", "Please fill in all fields for page replacement.")
            return
        try:
            dest = replace_pages(inp, out, target_pages=target, replacement_pdf_path=src)
            messagebox.showinfo("Success", f"Replaced target pages saved to:\n{dest}")
            self.set_status(f"Replaced pages saved to '{dest}'")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    # -------------------------------------------------------------------------
    # TAB 5: Text Editor (Sejda-style)
    # -------------------------------------------------------------------------
    def setup_text_tab(self):
        frame = self.tab_text
        frame.grid_columnconfigure(1, weight=1)

        # Common Input / Output File Selectors
        lbl_in = ctk.CTkLabel(frame, text="Input PDF:", font=ctk.CTkFont(weight="bold"))
        lbl_in.grid(row=0, column=0, sticky="w", padx=15, pady=(15, 5))
        self.text_in_entry = ctk.CTkEntry(frame)
        self.text_in_entry.grid(row=0, column=1, sticky="ew", padx=10, pady=(15, 5))
        btn_in = ctk.CTkButton(frame, text="Browse...", width=90, command=lambda: self.browse_file_to_entry(self.text_in_entry))
        btn_in.grid(row=0, column=2, padx=15, pady=(15, 5))

        lbl_out = ctk.CTkLabel(frame, text="Output PDF:", font=ctk.CTkFont(weight="bold"))
        lbl_out.grid(row=1, column=0, sticky="w", padx=15, pady=5)
        self.text_out_entry = ctk.CTkEntry(frame)
        self.text_out_entry.grid(row=1, column=1, sticky="ew", padx=10, pady=5)
        btn_out = ctk.CTkButton(frame, text="Browse...", width=90, command=lambda: self.browse_save_to_entry(self.text_out_entry))
        btn_out.grid(row=1, column=2, padx=15, pady=5)

        # Sub-tabview for Text Operations
        text_sub = ctk.CTkTabview(frame)
        text_sub.grid(row=2, column=0, columnspan=3, sticky="nsew", padx=15, pady=10)
        frame.grid_rowconfigure(2, weight=1)

        tab_repl = text_sub.add("Find & Replace Text")
        tab_add = text_sub.add("Add Text Overlay")
        tab_redact = text_sub.add("Redact / Blackout Text")
        tab_search = text_sub.add("Search Text")

        # 1. Find & Replace Text
        ctk.CTkLabel(tab_repl, text="Find Text String:").pack(anchor="w", padx=15, pady=(10, 2))
        self.txt_find_entry = ctk.CTkEntry(tab_repl)
        self.txt_find_entry.pack(fill="x", padx=15, pady=2)

        ctk.CTkLabel(tab_repl, text="Replace With Text String:").pack(anchor="w", padx=15, pady=(10, 2))
        self.txt_repl_entry = ctk.CTkEntry(tab_repl)
        self.txt_repl_entry.pack(fill="x", padx=15, pady=2)

        ctk.CTkLabel(tab_repl, text="Target Pages (e.g. 'all' or '1-3'):").pack(anchor="w", padx=15, pady=(10, 2))
        self.txt_pages_entry = ctk.CTkEntry(tab_repl)
        self.txt_pages_entry.insert(0, "all")
        self.txt_pages_entry.pack(fill="x", padx=15, pady=2)

        btn_do_text_repl = ctk.CTkButton(tab_repl, text="Execute Find & Replace", font=ctk.CTkFont(weight="bold"), fg_color="green", hover_color="darkgreen", command=self.text_do_replace)
        btn_do_text_repl.pack(padx=15, pady=20)

        # 2. Add Text Overlay
        ctk.CTkLabel(tab_add, text="Text Content to Add:").pack(anchor="w", padx=15, pady=(10, 2))
        self.txt_add_content = ctk.CTkEntry(tab_add)
        self.txt_add_content.pack(fill="x", padx=15, pady=2)

        pos_frame = ctk.CTkFrame(tab_add, fg_color="transparent")
        pos_frame.pack(fill="x", padx=15, pady=10)

        ctk.CTkLabel(pos_frame, text="Page:").pack(side="left", padx=5)
        self.txt_add_page = ctk.CTkEntry(pos_frame, width=60)
        self.txt_add_page.insert(0, "1")
        self.txt_add_page.pack(side="left", padx=5)

        ctk.CTkLabel(pos_frame, text="X (px):").pack(side="left", padx=5)
        self.txt_add_x = ctk.CTkEntry(pos_frame, width=80)
        self.txt_add_x.insert(0, "100.0")
        self.txt_add_x.pack(side="left", padx=5)

        ctk.CTkLabel(pos_frame, text="Y (px):").pack(side="left", padx=5)
        self.txt_add_y = ctk.CTkEntry(pos_frame, width=80)
        self.txt_add_y.insert(0, "100.0")
        self.txt_add_y.pack(side="left", padx=5)

        ctk.CTkLabel(pos_frame, text="Font Size:").pack(side="left", padx=5)
        self.txt_add_size = ctk.CTkEntry(pos_frame, width=60)
        self.txt_add_size.insert(0, "14.0")
        self.txt_add_size.pack(side="left", padx=5)

        btn_do_add_txt = ctk.CTkButton(tab_add, text="Add Text Overlay", font=ctk.CTkFont(weight="bold"), fg_color="green", hover_color="darkgreen", command=self.text_do_add)
        btn_do_add_txt.pack(padx=15, pady=15)

        # 3. Redact / Blackout Text
        ctk.CTkLabel(tab_redact, text="Target Text to Redact / Blackout:").pack(anchor="w", padx=15, pady=(15, 2))
        self.txt_redact_entry = ctk.CTkEntry(tab_redact)
        self.txt_redact_entry.pack(fill="x", padx=15, pady=5)

        btn_do_redact = ctk.CTkButton(tab_redact, text="Blackout / Redact Text", font=ctk.CTkFont(weight="bold"), fg_color="firebrick", hover_color="darkred", command=self.text_do_redact)
        btn_do_redact.pack(padx=15, pady=20)

        # 4. Search Text
        search_top = ctk.CTkFrame(tab_search, fg_color="transparent")
        search_top.pack(fill="x", padx=15, pady=10)

        ctk.CTkLabel(search_top, text="Query:").pack(side="left", padx=5)
        self.txt_search_entry = ctk.CTkEntry(search_top)
        self.txt_search_entry.pack(side="left", fill="x", expand=True, padx=5)

        btn_do_search = ctk.CTkButton(search_top, text="Search", width=90, command=self.text_do_search)
        btn_do_search.pack(side="right", padx=5)

        self.search_results_box = ctk.CTkTextbox(tab_search, font=ctk.CTkFont(family="Consolas", size=12))
        self.search_results_box.pack(fill="both", expand=True, padx=15, pady=(5, 10))

    def text_do_replace(self):
        inp, out = self.text_in_entry.get().strip(), self.text_out_entry.get().strip()
        old_txt = self.txt_find_entry.get()
        new_txt = self.txt_repl_entry.get()
        pages = self.txt_pages_entry.get().strip()
        if not inp or not out or not old_txt:
            messagebox.showwarning("Warning", "Please provide input PDF, output PDF, and text to find.")
            return
        try:
            count = replace_text(inp, out, old_txt, new_txt, pages=pages)
            messagebox.showinfo("Success", f"Replaced {count} occurrences of '{old_txt}'.\nSaved to: {out}")
            self.set_status(f"Replaced {count} text occurrences in '{out}'")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def text_do_add(self):
        inp, out = self.text_in_entry.get().strip(), self.text_out_entry.get().strip()
        txt = self.txt_add_content.get()
        if not inp or not out or not txt:
            messagebox.showwarning("Warning", "Please provide input PDF, output PDF, and text content.")
            return
        try:
            p_num = int(self.txt_add_page.get().strip())
            x = float(self.txt_add_x.get().strip())
            y = float(self.txt_add_y.get().strip())
            size = float(self.txt_add_size.get().strip())
            dest = add_text(inp, out, txt, page_num=p_num, x=x, y=y, font_size=size)
            messagebox.showinfo("Success", f"Added text overlay saved to:\n{dest}")
            self.set_status(f"Added text overlay to page {p_num}")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def text_do_redact(self):
        inp, out = self.text_in_entry.get().strip(), self.text_out_entry.get().strip()
        target = self.txt_redact_entry.get().strip()
        if not inp or not out or not target:
            messagebox.showwarning("Warning", "Please provide input PDF, output PDF, and target text to redact.")
            return
        try:
            count = redact_text(inp, out, target)
            messagebox.showinfo("Success", f"Redacted {count} occurrences of '{target}'.\nSaved to: {out}")
            self.set_status(f"Redacted {count} text occurrences in '{out}'")
        except Exception as e:
            messagebox.showerror("Error", str(e))

    def text_do_search(self):
        inp = self.text_in_entry.get().strip()
        q = self.txt_search_entry.get().strip()
        if not inp or not q:
            messagebox.showwarning("Warning", "Please select an input PDF and enter a search query.")
            return
        try:
            results = search_text(inp, q)
            self.search_results_box.delete("1.0", tk.END)
            if not results:
                self.search_results_box.insert("1.0", f"No occurrences of '{q}' found in document.")
            else:
                text = f"Found {len(results)} match(es) for '{q}':\n" + "=" * 60 + "\n"
                for r in results:
                    text += f"Page {r['page']:02d} | Box: {r['rect']} | Snippet: \"{r['snippet']}\"\n"
                self.search_results_box.insert("1.0", text)
            self.set_status(f"Search found {len(results)} match(es)")
        except Exception as e:
            messagebox.showerror("Error", str(e))


def launch_gui():
    app = PDFEditorApp()
    app.mainloop()


if __name__ == "__main__":
    launch_gui()
