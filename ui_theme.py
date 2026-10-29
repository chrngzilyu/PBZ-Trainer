"""Original dark trainer theme, using only Tk/ttk."""
import tkinter as tk
from tkinter import ttk

BG = '#111318'
CARD = '#1b1e25'
FG = '#eceef3'
MUTED = '#929aa9'
ACCENT = '#e45d62'


def configure(root):
    root.configure(bg=BG)
    root.after_idle(lambda: dark_titlebar(root))
    style = ttk.Style(root)
    style.theme_use('clam')
    style.configure('.', font=('Microsoft YaHei UI', 10), background=BG, foreground=FG)
    style.configure('TFrame', background=BG)
    style.configure('TLabel', background=BG, foreground=FG)
    style.configure('Card.TFrame', background=CARD)
    style.configure('Card.TLabel', background=CARD)
    style.configure('Title.TLabel', font=('Microsoft YaHei UI', 24, 'bold'))
    style.configure('Hint.TLabel', foreground=MUTED, font=('Microsoft YaHei UI', 9))
    style.configure('Muted.Card.TLabel', background=CARD, foreground=MUTED, font=('Microsoft YaHei UI', 9))
    style.configure('Heading.Card.TLabel', background=CARD, font=('Microsoft YaHei UI', 11, 'bold'))
    style.configure('Value.Card.TLabel', background=CARD, font=('Microsoft YaHei UI', 16, 'bold'))
    style.configure('TButton', background='#2a2e38', foreground=FG, borderwidth=0, padding=(12, 7), focusthickness=1, focuscolor=ACCENT)
    style.map('TButton', background=[('disabled', '#242731'), ('pressed', '#3a3f4d'), ('active', '#363b48')], foreground=[('disabled', '#677080')])
    style.configure('Accent.TButton', background=ACCENT, foreground='#ffffff')
    style.map('Accent.TButton', background=[('disabled', '#493036'), ('pressed', '#bc424b'), ('active', '#f07277')])
    style.configure('TSpinbox', fieldbackground='#101218', background='#2a2e38', foreground=FG,
                    arrowcolor=MUTED, bordercolor='#373d4b', lightcolor='#373d4b', darkcolor='#373d4b', padding=(7, 6), insertcolor=FG)
    style.map('TSpinbox', foreground=[('disabled', '#677080')], fieldbackground=[('disabled', '#191c23')])
    style.configure('TNotebook', background=BG, borderwidth=0, tabmargins=(0, 0, 0, 12))
    style.configure('TNotebook.Tab', background=BG, foreground=MUTED, padding=(18, 9), borderwidth=0)
    style.map('TNotebook.Tab', background=[('selected', CARD), ('active', '#242832')], foreground=[('selected', FG)])
    style.configure('Vertical.TScrollbar', background='#393e4b', troughcolor=BG, borderwidth=0, arrowsize=11)
    style.configure('TSeparator', background='#30343e')
    images=[]
    for active, disabled in ((False, False), (True, False), (False, True), (True, True)):
        image=tk.PhotoImage(master=root, width=44, height=24)
        image.put(CARD, to=(0, 0, 44, 24))
        track=('#74434a' if disabled else ACCENT) if active else ('#282c35' if disabled else '#444b59')
        knob='#898e99' if disabled else '#ffffff'
        for y in range(24):
            for x in range(44):
                edge=12 if x < 12 else 31 if x > 31 else x
                if (x-edge)**2+(y-11.5)**2 <= 11.5**2:
                    image.put(track, to=(x,y))
                if (x-(32 if active else 11))**2+(y-11.5)**2 <= 8.5**2:
                    image.put(knob, to=(x,y))
        images.append(image)
    root._switch_images=images
    style.element_create('TrainerSwitch.indicator', 'image', images[0],
                         ('disabled', 'selected', images[3]), ('disabled', images[2]), ('selected', images[1]), sticky='e')
    style.layout('Switch.TCheckbutton', [('Checkbutton.padding', {'sticky':'nswe', 'children':[
        ('TrainerSwitch.indicator', {'side':'right', 'sticky':'e'}),
        ('Checkbutton.focus', {'side':'left', 'sticky':'w', 'children':[('Checkbutton.label', {'sticky':'w'})]})]})])
    style.configure('Switch.TCheckbutton', background=CARD, foreground=FG, padding=(0, 1), font=('Microsoft YaHei UI', 10))
    style.map('Switch.TCheckbutton', foreground=[('disabled', '#707888')], background=[('active', CARD)])
    return style


class Tooltip:
    def __init__(self, widget, text):
        self.widget, self.text = widget, text
        self.pending = self.window = None
        widget.bind('<Enter>', self.schedule, add='+')
        widget.bind('<Leave>', self.hide, add='+')
        widget.bind('<FocusIn>', self.schedule, add='+')
        widget.bind('<FocusOut>', self.hide, add='+')
        widget.bind('<ButtonPress>', self.hide, add='+')
        widget.bind('<Destroy>', self.hide, add='+')

    def schedule(self, event=None):
        self.hide()
        self.pending = self.widget.after(550, self.show)

    def show(self):
        self.pending = None
        if not self.widget.winfo_exists():
            return
        self.window = tk.Toplevel(self.widget)
        self.window.withdraw()
        self.window.overrideredirect(True)
        tk.Label(self.window, text=self.text, bg='#343a48', fg=FG, font=('Microsoft YaHei UI', 9),
                 wraplength=360, justify='left', padx=12, pady=8).pack()
        self.window.update_idletasks()
        x = min(self.widget.winfo_rootx() + 12, self.widget.winfo_screenwidth() - self.window.winfo_reqwidth() - 12)
        y = min(self.widget.winfo_rooty() + self.widget.winfo_height() + 5,
                self.widget.winfo_screenheight() - self.window.winfo_reqheight() - 40)
        self.window.geometry(f'+{max(0,x)}+{max(0,y)}')
        self.window.deiconify()

    def hide(self, event=None):
        if self.pending is not None:
            self.widget.after_cancel(self.pending)
            self.pending = None
        if self.window is not None:
            self.window.destroy()
            self.window = None


def dark_titlebar(root):
    """Match this window's Windows title bar to the theme, when supported."""
    import ctypes
    try:
        user32 = ctypes.WinDLL('user32')
        user32.GetParent.argtypes = [ctypes.c_void_p]
        user32.GetParent.restype = ctypes.c_void_p
        handle = user32.GetParent(root.winfo_id()) or root.winfo_id()
        dwm = ctypes.WinDLL('dwmapi')
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p, ctypes.c_uint]
        enabled = ctypes.c_int(1)
        if dwm.DwmSetWindowAttribute(handle, 20, ctypes.byref(enabled), ctypes.sizeof(enabled)) != 0:
            dwm.DwmSetWindowAttribute(handle, 19, ctypes.byref(enabled), ctypes.sizeof(enabled))
    except (OSError, tk.TclError):
        pass
