#include <pybind11/pybind11.h>
#include <pybind11/stl.h>
#include "tuber_support.hpp"

namespace py = pybind11;

#include <algorithm>

enum class Kind { X, Y };

class Wrapper {
	public:
		Kind return_x() const { return Kind::X; }
		Kind return_y() const { return Kind::Y; }

		bool is_x(Kind const& k) const { return k == Kind::X; }
		bool is_y(Kind const& k) const { return k == Kind::Y; }

		std::vector<int> increment(std::vector<int> x) {
			std::ranges::for_each(x, [](int &n) { n++; });
			return x;
		};
};

PYBIND11_MODULE(test_module, m) {

	py::str_enum<Kind> kind(m, "Kind");
	kind.value("X", Kind::X)
		.value("Y", Kind::Y);

	py::class_<Wrapper>(m, "Wrapper")
		.def(py::init())
		.def("return_x", &Wrapper::return_x)
		.def("return_y", &Wrapper::return_y)
		.def("is_x", &Wrapper::is_x)
		.def("is_y", &Wrapper::is_y)
		.def("increment", &Wrapper::increment);
}
