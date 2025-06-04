from docker_runner.DockerCodeRunner import DockerCodeRunner
runner = DockerCodeRunner()

code = """
import numpy as np

def test(matrix):
    arr = np.array(matrix)
    return np.array_equal(arr, arr.T)
"""

tests = [
    {
        "id": 1,
        "parameters": [[[1, 2], [2, 1]]],
        "results": [True]
    },
    {
        "id": 2,
        "parameters": [[[1, 0], [2, 1]]],
        "results": [False]
    }
]

result = runner.run(
    image_name="python:3.11",  # без предсобранного образа
    user_code=code,
    libraries=["numpy"],       # важно!
    tests=tests,
    script_name="test",
    script_parameters=["matrix"],
    cleanup=False
)

import pprint
pprint.pprint(result)
