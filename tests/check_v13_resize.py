import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import os,tempfile,time
from pathlib import Path
from PIL import Image, ImageDraw
from unittest.mock import patch
with tempfile.TemporaryDirectory(prefix="xdrive-v13-") as tmp:
    root=Path(tmp)/"files";root.mkdir()
    os.environ["XDRIVE_SEARCH_DB_DIR"]=str(Path(tmp)/"db")
    os.environ["XDRIVE_SEARCH_ROOT"]=str(root)
    import XDriveSearch as m
    pdf=root/"Drawing.pdf"
    img=Image.new("RGB",(1400,1000),"white")
    draw=ImageDraw.Draw(img)
    for x in range(20,1400,20):
        draw.line((x,10,x,990),fill="black",width=1)
    img.save(pdf)
    for name in ("Part10.step","Part2.step"):
        (root/name).write_bytes(b"x")
    idx=m.Indexer();assert idx.build_index(root)
    assert [r[0] for r in idx.search("Part",sort_col="name",descending=False)]==["Part2.step","Part10.step"]
    # Map only off-screen, never drive actual mouse/keyboard or another application.
    original=m.BaseTk.__init__
    def offscreen(self,*args,**kwargs):
        original(self,*args,**kwargs)
        self.withdraw()
    with patch.object(m.BaseTk,"__init__",offscreen):
        app=m.App()
    def spin(seconds=.1,predicate=None):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            app.update()
            if predicate and predicate():return True
            time.sleep(.015)
        return bool(predicate and predicate())
    try:
        app.geometry("1200x850+30000+30000")
        app.deiconify()
        spin(.2)
        app.keyword_var.set("Drawing")
        spin(.5)
        items=app.tree.get_children();assert len(items)==1
        app.tree.selection_set(items[0]);app.tree.focus(items[0])
        app._schedule_preview()
        assert spin(8,lambda:app._preview_img is not None)
        canvas=app.preview_canvas;handle=app.preview_handle
        old_height=canvas.winfo_height()
        old_source=max(app._preview_img.size)
        old_render=app._preview_photo.height()
        y=handle.winfo_rooty()+3
        handle.event_generate("<ButtonPress-1>",x=5,y=3,rootx=handle.winfo_rootx()+5,rooty=y)
        handle.event_generate("<B1-Motion>",x=5,y=303,rootx=handle.winfo_rootx()+5,rooty=y+300)
        handle.event_generate("<ButtonRelease-1>",x=5,y=303,rootx=handle.winfo_rootx()+5,rooty=y+300)
        spin(.2)
        print("HEIGHT before/after",old_height,canvas.winfo_height(),"TABLE",app.results_frame.winfo_height())
        assert canvas.winfo_height()>=old_height+240
        assert app.results_frame.winfo_height()>=100
        assert spin(8,lambda:app._preview_img is not None and max(app._preview_img.size)>old_source)
        assert app._preview_photo.height()>=old_render+200
        print("PDF source resolution",old_source,"->",max(app._preview_img.size),"render height",old_render,"->",app._preview_photo.height())
        assert app.tree.selection()
        app._preview_reset_size()
        spin(.2)
        assert canvas.winfo_height()<=old_height+5
        print("PASS reset size")
        app._set_preview_height(10000)
        spin(.2)
        assert app.results_frame.winfo_height()>=100
        app.geometry("1100x520+30000+30000")
        spin(.25)
        print("SMALL WINDOW",canvas.winfo_height(),app.results_frame.winfo_height())
        assert app.results_frame.winfo_height()>=80
        print("PASS minimum table height on window shrink")
        app.tree.selection_remove(*app.tree.selection());app._schedule_preview();spin(.4)
        assert app._preview_img is None
        print("PASS clear preview, sorting, no stale image")
    finally:
        app.destroy()
    print("ALL_TESTS_PASS")
