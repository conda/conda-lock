
import sys

if sys.version_info[0] == 2:
	from conda_lock.vendor.conda._vendor.cpuinfo import *
else:
	from conda_lock._vendor.conda._vendor.cpuinfo.cpuinfo import *


