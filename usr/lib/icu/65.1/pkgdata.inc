GENCCODE_ASSEMBLY_TYPE=-a gcc
SO=so
SOBJ=so
A=a
LIBPREFIX=lib
LIB_EXT_ORDER=.65.1
COMPILE=/home/FiiO/work/ECHO/buildroot/buildroot/output/host/bin/mips-linux-gnu-gcc -D_LARGEFILE_SOURCE -D_LARGEFILE64_SOURCE -D_FILE_OFFSET_BITS=64 -D__USE_TIME_BITS64 -D_REENTRANT  -DU_HAVE_ELF_H=1 -DU_HAVE_STRTOD_L=1 -DU_HAVE_XLOCALE_H=0  -DU_ATTRIBUTE_DEPRECATED= -D_LARGEFILE_SOURCE -D_LARGEFILE64_SOURCE -D_FILE_OFFSET_BITS=64 -D__USE_TIME_BITS64  -Os   -std=c11 -pedantic -Wshadow -Wpointer-arith -Wmissing-prototypes -Wwrite-strings   -c
LIBFLAGS=-I/usr/include -DPIC -fPIC
GENLIB=/home/FiiO/work/ECHO/buildroot/buildroot/output/host/bin/mips-linux-gnu-gcc -D_LARGEFILE_SOURCE -D_LARGEFILE64_SOURCE -D_FILE_OFFSET_BITS=64 -D__USE_TIME_BITS64  -Os   -std=c11 -pedantic -Wshadow -Wpointer-arith -Wmissing-prototypes -Wwrite-strings    -shared -Wl,-Bsymbolic
LDICUDTFLAGS=
LD_SONAME=-Wl,-soname -Wl,
RPATH_FLAGS=
BIR_LDFLAGS=-Wl,-Bsymbolic
AR=/home/FiiO/work/ECHO/buildroot/buildroot/output/host/bin/mips-linux-gnu-ar
ARFLAGS=r
RANLIB=/home/FiiO/work/ECHO/buildroot/buildroot/output/host/bin/mips-linux-gnu-ranlib
INSTALL_CMD=/usr/bin/install -c
