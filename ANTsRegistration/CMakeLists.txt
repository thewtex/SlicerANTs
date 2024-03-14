#-----------------------------------------------------------------------------
set(MODULE_NAME ANTsRegistration)

#-----------------------------------------------------------------------------
set(MODULE_PYTHON_SCRIPTS
  ${MODULE_NAME}.py
  antsRegistrationLib/__init__.py
  antsRegistrationLib/util.py
  antsRegistrationLib/Widgets/__init__.py
  antsRegistrationLib/Widgets/delegates.py
  antsRegistrationLib/Widgets/tables.py
  )

set(MODULE_PYTHON_RESOURCES
  Resources/Icons/${MODULE_NAME}.png
  Resources/Presets/QuickSyN.json
  Resources/Presets/Rigid.json
  Resources/Presets/Rigid+Affine.json
  Resources/UI/${MODULE_NAME}.ui
  )

#-----------------------------------------------------------------------------
slicerMacroBuildScriptedModule(
  NAME ${MODULE_NAME}
  SCRIPTS ${MODULE_PYTHON_SCRIPTS}
  RESOURCES ${MODULE_PYTHON_RESOURCES}
  WITH_GENERIC_TESTS
  )

#-----------------------------------------------------------------------------
if(BUILD_TESTING)

  # Register the unittest subclass in the main script as a ctest.
  # Note that the test will also be available at runtime.
  slicer_add_python_unittest(SCRIPT ${MODULE_NAME}.py)

  # Additional build-time testing
  add_subdirectory(Testing)
endif()
