import time
from alpha import getDCFArray

t = time.time()
dcfs =  getDCFArray("FB",5)
print(f"Elapsed time: {time.time() - t}s")
print(dcfs)