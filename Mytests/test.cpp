
#include "pch.h"
#include <gtest/gtest.h>
#include "my_math.h"

// 测试 Add 函数
TEST(MyMathTest, Add) {
    EXPECT_EQ(Add(1, 1), 2);
    EXPECT_EQ(Add(-1, 1), 0);
    EXPECT_EQ(Add(0, 0), 0);
}

// 测试 Subtract 函数
TEST(MyMathTest, Subtract) {
    EXPECT_EQ(Subtract(2, 1), 1);
    EXPECT_EQ(Subtract(1, 1), 0);
    EXPECT_EQ(Subtract(1, 2), -1);
}

// 主函数运行所有测试
int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
