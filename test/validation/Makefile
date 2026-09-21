SIM ?= verilator
SUITE ?= soc
TOPLEVEL_LANG = verilog
TOPLEVEL = tb_$(SUITE)
COCOTB_TEST_MODULES = test_$(SUITE)
HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
SRC := $(HERE)/../../src
SIM_BUILD ?= $(HERE)/build/$(SIM)-$(SUITE)
SPI_DIV ?= 1
export PYTHONPATH := $(HERE):$(PYTHONPATH)
VERILOG_SOURCES = $(SRC)/project.sv $(SRC)/neuron.sv $(SRC)/spi_master.v $(HERE)/benches.sv
COMPILE_ARGS += -DNPU_SPI_DIV=$(SPI_DIV)
ifeq ($(SIM),verilator)
  EXTRA_ARGS += --no-timing -Wno-fatal
else ifeq ($(SIM),icarus)
  COMPILE_ARGS += -g2012
endif
include $(shell cocotb-config --makefiles)/Makefile.sim
