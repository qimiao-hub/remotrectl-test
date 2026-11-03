cmake_minimum_required(VERSION 3.16)
project(sim_dds_bridge LANGUAGES CXX)
set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(REMOTECTL_SOURCE_DIR "${CMAKE_CURRENT_LIST_DIR}/../../../../remotectl" CACHE PATH "remotectl source directory")
find_package(fastrtps 2 REQUIRED)
find_package(fastcdr REQUIRED)
find_package(Threads REQUIRED)

# 复用同一套生成类型；此目标可单独在 Windows/Linux 构建，不引入遥控端 POSIX/YAML 业务。
set(TYPE_NAMES ChassisCtrl gps_imu_msg gps_msg imple_msg safe_ctrl_msg obst_msg v2x_msg vehicle_task_msg
    collection_control_request collection_control_reply collection_status_report)
set(SOURCES
    "${REMOTECTL_SOURCE_DIR}/tools/sim_dds_bridge.cpp"
    "${REMOTECTL_SOURCE_DIR}/include/ddsType/Cockpit_address.cxx"
    "${REMOTECTL_SOURCE_DIR}/include/ddsType/Cockpit_addressPubSubTypes.cxx"
)
foreach(type_name IN LISTS TYPE_NAMES)
    list(APPEND SOURCES
        "${REMOTECTL_SOURCE_DIR}/dvpn-pnc-common/ddsType/src/${type_name}.cxx"
        "${REMOTECTL_SOURCE_DIR}/dvpn-pnc-common/ddsType/src/${type_name}PubSubTypes.cxx"
    )
endforeach()
add_executable(sim-dds-bridge ${SOURCES})
target_include_directories(sim-dds-bridge PRIVATE
    "${REMOTECTL_SOURCE_DIR}/include"
    "${REMOTECTL_SOURCE_DIR}/include/ddsType"
    "${REMOTECTL_SOURCE_DIR}/dvpn-pnc-common/ddsType/include"
)
target_link_libraries(sim-dds-bridge PRIVATE fastrtps fastcdr Threads::Threads)
set_target_properties(sim-dds-bridge PROPERTIES RUNTIME_OUTPUT_DIRECTORY "${CMAKE_BINARY_DIR}/bin")
# 多配置生成器也输出到同一目录，使 bridge_config.json 无须区分 Debug/Release。
foreach(build_type DEBUG RELEASE RELWITHDEBINFO MINSIZEREL)
    set_target_properties(sim-dds-bridge PROPERTIES "RUNTIME_OUTPUT_DIRECTORY_${build_type}" "${CMAKE_BINARY_DIR}/bin")
endforeach()
