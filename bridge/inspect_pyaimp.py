import pyaimp
try:
    c = pyaimp.Client()
    print(f"Client attributes: {dir(c)}")
    if hasattr(c, 'get_playlist_manager'):
        pm = c.get_playlist_manager()
        print(f"PM attributes: {dir(pm)}")
except Exception as e:
    print(f"Error: {e}")
