import io
import tkinter as tk
from tkinter import filedialog, colorchooser, messagebox, ttk
from PIL import Image, ImageDraw, ImageTk
import pymupdf  # PyMuPDF


class PDFDoodleEditor:
    def __init__(self, root):
        self.root = root
        self.root.title("PDF Doodle Editor")
        self.root.geometry("1200x850")
        self.root.minsize(800, 600)

        self.doc = None
        self.pdf_path = None
        self.page_index = 0
        self.color = "#ff3030"
        self.brush_size = tk.IntVar(value=4)
        self.filled_rect = tk.BooleanVar(value=False)
        self.tool = tk.StringVar(value="draw")
        self.page_edits = {}
        self.preview_photo = None
        self.page_image = None
        self.page_rect = None
        self.view_scale = 1.0
        self.drag_start = None
        self.current_preview_id = None
        self.image_to_insert = None
        self.temp_item_ids = []
        self.undo_stack = {}

        self._build_ui()
        self.root.bind("<Control-o>", lambda _e: self.open_pdf())
        self.root.bind("<Control-s>", lambda _e: self.export_pdf())
        self.root.bind("<Control-z>", lambda _e: self.undo())

    def _build_ui(self):
        toolbar = ttk.Frame(self.root, padding=6)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(toolbar, text="Open PDF", command=self.open_pdf).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Export flattened PDF", command=self.export_pdf).pack(side=tk.LEFT, padx=2)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=7)

        for label, value in [("Draw", "draw"), ("Rectangle", "rect"), ("Insert image", "image")]:
            ttk.Radiobutton(toolbar, text=label, value=value, variable=self.tool,
                            command=self._tool_changed).pack(side=tk.LEFT, padx=2)

        ttk.Button(toolbar, text="Choose color", command=self.choose_color).pack(side=tk.LEFT, padx=(8, 2))
        self.color_swatch = tk.Label(toolbar, bg=self.color, width=3, relief=tk.SUNKEN)
        self.color_swatch.pack(side=tk.LEFT, padx=3)

        ttk.Label(toolbar, text="Brush (pt):").pack(side=tk.LEFT, padx=(8, 2))
        ttk.Spinbox(toolbar, from_=1, to=40, width=4, textvariable=self.brush_size).pack(side=tk.LEFT)
        ttk.Checkbutton(toolbar, text="Filled rectangle", variable=self.filled_rect).pack(side=tk.LEFT, padx=8)
        ttk.Button(toolbar, text="Undo", command=self.undo).pack(side=tk.LEFT, padx=2)
        ttk.Button(toolbar, text="Clear page edits", command=self.clear_page).pack(side=tk.LEFT, padx=2)

        nav = ttk.Frame(self.root, padding=(6, 0, 6, 6))
        nav.pack(side=tk.TOP, fill=tk.X)
        ttk.Button(nav, text="◀ Previous", command=self.previous_page).pack(side=tk.LEFT)
        ttk.Button(nav, text="Next ▶", command=self.next_page).pack(side=tk.LEFT, padx=4)
        self.page_label = ttk.Label(nav, text="No PDF open")
        self.page_label.pack(side=tk.LEFT, padx=10)
        ttk.Label(nav, text="Tip: for an image, choose Insert image, select a file, then drag out its placement area.").pack(side=tk.RIGHT)

        self.canvas = tk.Canvas(self.root, bg="#555555", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Configure>", lambda _e: self.render_page())

        self.status = ttk.Label(self.root, text="Open a PDF to begin. Shortcuts: Ctrl+O open, Ctrl+S export, Ctrl+Z undo.", padding=5)
        self.status.pack(side=tk.BOTTOM, fill=tk.X)

    def open_pdf(self):
        path = filedialog.askopenfilename(
            title="Open PDF",
            filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")]
        )
        if not path:
            return
        try:
            new_doc = pymupdf.open(path)
            if len(new_doc) == 0:
                raise ValueError("The PDF has no pages.")
        except Exception as exc:
            messagebox.showerror("Could not open PDF", str(exc))
            return

        if self.doc:
            self.doc.close()
        self.doc = new_doc
        self.pdf_path = path
        self.page_index = 0
        self.page_edits = {}
        self.undo_stack = {}
        self.render_page()
        self.status.config(text=f"Opened: {path}")

    def choose_color(self):
        chosen = colorchooser.askcolor(color=self.color, title="Choose doodle color")
        if chosen and chosen[1]:
            self.color = chosen[1]
            self.color_swatch.config(bg=self.color)

    def _tool_changed(self):
        self.image_to_insert = None
        if self.tool.get() == "image":
            path = filedialog.askopenfilename(
                title="Choose image to insert",
                filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp *.gif *.webp"), ("All files", "*.*")]
            )
            if not path:
                self.tool.set("draw")
                return
            try:
                image = Image.open(path).convert("RGBA")
                if image.width < 1 or image.height < 1:
                    raise ValueError("The image is empty.")
                self.image_to_insert = image
                self.status.config(text=f"Selected image: {path}. Drag on the page to place and resize it.")
            except Exception as exc:
                messagebox.showerror("Could not open image", str(exc))
                self.tool.set("draw")

    def _page(self):
        if self.doc is None:
            return None
        return self.doc.load_page(self.page_index)

    def _get_edits(self):
        return self.page_edits.setdefault(self.page_index, [])

    def _push_undo(self):
        edits = self._get_edits()
        self.undo_stack.setdefault(self.page_index, []).append(list(edits))

    def _canvas_page_box(self):
        if self.page_rect is None:
            return None
        return self.page_rect

    def _event_to_page(self, x, y):
        box = self._canvas_page_box()
        if box is None or self.view_scale <= 0:
            return None
        left, top, right, bottom = box
        if not (left <= x <= right and top <= y <= bottom):
            return None
        return ((x - left) / self.view_scale, (y - top) / self.view_scale)

    def render_page(self):
        if self.doc is None or not self.canvas.winfo_exists():
            return
        page = self._page()
        canvas_w = max(self.canvas.winfo_width(), 100)
        canvas_h = max(self.canvas.winfo_height(), 100)
        page_w, page_h = float(page.rect.width), float(page.rect.height)
        self.view_scale = min((canvas_w - 30) / page_w, (canvas_h - 30) / page_h)
        self.view_scale = max(self.view_scale, 0.05)
        pix = page.get_pixmap(matrix=pymupdf.Matrix(self.view_scale, self.view_scale), alpha=False)
        self.page_image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        self.preview_photo = ImageTk.PhotoImage(self.page_image)
        left = (canvas_w - pix.width) // 2
        top = (canvas_h - pix.height) // 2
        self.page_rect = (left, top, left + pix.width, top + pix.height)

        self.canvas.delete("all")
        self.canvas.create_image(left, top, image=self.preview_photo, anchor=tk.NW, tags="page")
        self._draw_existing_edits()
        self.page_label.config(text=f"Page {self.page_index + 1} of {len(self.doc)}")
        self.status.config(text=f"Tool: {self.tool.get()} | Color: {self.color} | Brush: {self.brush_size.get()} pt")

    def _draw_existing_edits(self):
        if self.page_rect is None:
            return
        left, top, _, _ = self.page_rect
        s = self.view_scale
        for edit in self._get_edits():
            kind = edit["kind"]
            if kind == "draw":
                pts = edit["points"]
                if len(pts) == 1:
                    x, y = pts[0]
                    r = max(1, edit["width"] * s / 2)
                    self.canvas.create_oval(left + x*s-r, top + y*s-r, left + x*s+r, top + y*s+r,
                                            fill=edit["color"], outline=edit["color"], tags="edit")
                elif len(pts) > 1:
                    flat = []
                    for x, y in pts:
                        flat.extend((left + x*s, top + y*s))
                    self.canvas.create_line(*flat, fill=edit["color"], width=max(1, edit["width"]*s),
                                            capstyle=tk.ROUND, joinstyle=tk.ROUND, smooth=True, tags="edit")
            elif kind == "rect":
                x1, y1, x2, y2 = edit["box"]
                opts = {"outline": edit["color"], "width": max(1, edit["width"]*s), "tags": "edit"}
                if edit["filled"]:
                    opts["fill"] = edit["color"]
                self.canvas.create_rectangle(left+x1*s, top+y1*s, left+x2*s, top+y2*s, **opts)
            elif kind == "image":
                x1, y1, x2, y2 = edit["box"]
                overlay = edit["image"].resize((max(1, int((x2-x1)*s)), max(1, int((y2-y1)*s))), Image.Resampling.LANCZOS)
                photo = ImageTk.PhotoImage(overlay)
                self.temp_item_ids.append(photo)  # retain Tk image references
                self.canvas.create_image(left+x1*s, top+y1*s, image=photo, anchor=tk.NW, tags="edit")

    def on_press(self, event):
        point = self._event_to_page(event.x, event.y)
        if point is None or self.doc is None:
            return
        self.drag_start = point
        self._push_undo()
        tool = self.tool.get()
        if tool == "draw":
            self._get_edits().append({
                "kind": "draw", "points": [point], "color": self.color,
                "width": max(1, int(self.brush_size.get()))
            })
            self.render_page()
        elif tool in ("rect", "image"):
            self.current_preview_id = self.canvas.create_rectangle(
                event.x, event.y, event.x, event.y,
                outline=self.color if tool == "rect" else "#00aaff",
                width=2, dash=(4, 2), tags="temporary"
            )

    def on_drag(self, event):
        if self.drag_start is None or self.doc is None:
            return
        point = self._event_to_page(event.x, event.y)
        if point is None:
            left, top, right, bottom = self.page_rect
            point = (min(max((event.x-left)/self.view_scale, 0), (right-left)/self.view_scale),
                     min(max((event.y-top)/self.view_scale, 0), (bottom-top)/self.view_scale))
        tool = self.tool.get()
        if tool == "draw":
            edits = self._get_edits()
            if edits and edits[-1]["kind"] == "draw":
                edits[-1]["points"].append(point)
            self.render_page()
        else:
            left, top, _, _ = self.page_rect
            x1 = left + self.drag_start[0]*self.view_scale
            y1 = top + self.drag_start[1]*self.view_scale
            x2 = left + point[0]*self.view_scale
            y2 = top + point[1]*self.view_scale
            if self.current_preview_id is not None:
                self.canvas.coords(self.current_preview_id, x1, y1, x2, y2)

    def on_release(self, event):
        if self.drag_start is None or self.doc is None:
            return
        end = self._event_to_page(event.x, event.y)
        if end is None:
            left, top, right, bottom = self.page_rect
            end = (min(max((event.x-left)/self.view_scale, 0), (right-left)/self.view_scale),
                   min(max((event.y-top)/self.view_scale, 0), (bottom-top)/self.view_scale))
        start = self.drag_start
        tool = self.tool.get()

        if tool in ("rect", "image"):
            if self.current_preview_id is not None:
                self.canvas.delete(self.current_preview_id)
                self.current_preview_id = None
            x1, x2 = sorted((start[0], end[0]))
            y1, y2 = sorted((start[1], end[1]))
            if abs(x2-x1) >= 2 and abs(y2-y1) >= 2:
                if tool == "rect":
                    self._get_edits().append({
                        "kind": "rect", "box": (x1, y1, x2, y2), "color": self.color,
                        "width": max(1, int(self.brush_size.get())), "filled": self.filled_rect.get()
                    })
                elif self.image_to_insert is not None:
                    self._get_edits().append({
                        "kind": "image", "box": (x1, y1, x2, y2), "image": self.image_to_insert.copy()
                    })
            self.render_page()
        elif tool == "draw":
            self.render_page()
        self.drag_start = None

    def undo(self):
        if self.doc is None:
            return
        stack = self.undo_stack.get(self.page_index, [])
        if not stack:
            self.status.config(text="Nothing to undo on this page.")
            return
        self.page_edits[self.page_index] = stack.pop()
        self.render_page()

    def clear_page(self):
        if self.doc is None:
            return
        if not self._get_edits():
            return
        if messagebox.askyesno("Clear page edits", "Remove all doodles, shapes, and inserted images from this page?"):
            self._push_undo()
            self.page_edits[self.page_index] = []
            self.render_page()

    def previous_page(self):
        if self.doc and self.page_index > 0:
            self.page_index -= 1
            self.render_page()

    def next_page(self):
        if self.doc and self.page_index < len(self.doc) - 1:
            self.page_index += 1
            self.render_page()

    def export_pdf(self):
        if self.doc is None:
            messagebox.showinfo("No PDF", "Open a PDF first.")
            return
        default_name = "flattened_" + (self.pdf_path.rsplit("/", 1)[-1] if self.pdf_path else "output.pdf")
        path = filedialog.asksaveasfilename(
            title="Save flattened PDF",
            defaultextension=".pdf",
            initialfile=default_name,
            filetypes=[("PDF files", "*.pdf")]
        )
        if not path:
            return
        try:
            out_doc = pymupdf.open()
            render_scale = 2.5  # output resolution; raise for sharper output, at the cost of file size
            for index in range(len(self.doc)):
                page = self.doc.load_page(index)
                pix = page.get_pixmap(matrix=pymupdf.Matrix(render_scale, render_scale), alpha=False)
                base = Image.frombytes("RGB", [pix.width, pix.height], pix.samples).convert("RGBA")
                draw = ImageDraw.Draw(base, "RGBA")
                page_edits = self.page_edits.get(index, [])
                for edit in page_edits:
                    kind = edit["kind"]
                    if kind == "draw":
                        points = [(round(x*render_scale), round(y*render_scale)) for x, y in edit["points"]]
                        rgb = self._hex_to_rgb(edit["color"])
                        width = max(1, round(edit["width"]*render_scale))
                        if len(points) == 1:
                            x, y = points[0]
                            r = max(1, width // 2)
                            draw.ellipse((x-r, y-r, x+r, y+r), fill=rgb+(255,))
                        elif len(points) > 1:
                            draw.line(points, fill=rgb+(255,), width=width, joint="curve")
                            r = max(1, width // 2)
                            for x, y in (points[0], points[-1]):
                                draw.ellipse((x-r, y-r, x+r, y+r), fill=rgb+(255,))
                    elif kind == "rect":
                        x1, y1, x2, y2 = [round(v*render_scale) for v in edit["box"]]
                        rgb = self._hex_to_rgb(edit["color"])
                        width = max(1, round(edit["width"]*render_scale))
                        if edit["filled"]:
                            draw.rectangle((x1, y1, x2, y2), fill=rgb+(255,))
                        else:
                            draw.rectangle((x1, y1, x2, y2), outline=rgb+(255,), width=width)
                    elif kind == "image":
                        x1, y1, x2, y2 = [round(v*render_scale) for v in edit["box"]]
                        target_w, target_h = max(1, x2-x1), max(1, y2-y1)
                        overlay = edit["image"].resize((target_w, target_h), Image.Resampling.LANCZOS)
                        base.alpha_composite(overlay, (x1, y1))

                flattened = base.convert("RGB")
                buffer = io.BytesIO()
                flattened.save(buffer, format="PNG", optimize=True)
                new_page = out_doc.new_page(width=page.rect.width, height=page.rect.height)
                new_page.insert_image(new_page.rect, stream=buffer.getvalue())

            out_doc.save(path, garbage=4, deflate=True)
            out_doc.close()
            messagebox.showinfo("Export complete", f"Saved flattened PDF:\n{path}\n\nEach page is a single embedded image. Text is no longer selectable/searchable.")
            self.status.config(text=f"Exported flattened PDF: {path}")
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))

    @staticmethod
    def _hex_to_rgb(value):
        value = value.lstrip("#")
        return tuple(int(value[i:i+2], 16) for i in (0, 2, 4))

    def close(self):
        if self.doc:
            self.doc.close()


def main():
    root = tk.Tk()
    app = PDFDoodleEditor(root)
    root.protocol("WM_DELETE_WINDOW", lambda: (app.close(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    main()
