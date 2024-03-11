import logging
import os
from typing import Annotated, Optional

import numpy as np
import vtk
import time


import slicer
from slicer.i18n import tr as _
from slicer.i18n import translate
from slicer.ScriptedLoadableModule import *
from slicer.util import VTKObservationMixin
from slicer.parameterNodeWrapper import (
    parameterNodeWrapper,
    WithinRange,
)
from ITKANTsCommon import ITKANTsCommonLogic

from slicer import (
    vtkMRMLScalarVolumeNode,
    vtkMRMLTransformNode,
    vtkMRMLLinearTransformNode,
    vtkMRMLBSplineTransformNode,
    vtkMRMLGridTransformNode,
)

class ANTsRegistration(ScriptedLoadableModule):
    """Uses ScriptedLoadableModule base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent):
        ScriptedLoadableModule.__init__(self, parent)
        self.parent.title = _("ANTs Registration")
        self.parent.categories = [translate("qSlicerAbstractCoreModule", "Registration")]
        self.parent.dependencies = ["ITKANTsCommon"]
        self.parent.contributors = ["Dženan Zukić (Kitware Inc.)"]
        # _() function marks text as translatable to other languages
        self.parent.helpText = _("ANTs computes high-dimensional mapping to capture the statistics of brain structure and function.")
        # TODO: add grant number
        self.parent.acknowledgementText = _("""
This file was originally developed by Dženan Zukić, Kitware Inc.,
and was partially funded by NIH grant .
""")

        # Additional initialization step after application startup is complete
        slicer.app.connect("startupCompleted()", registerSampleData)


#
# Register sample data sets in Sample Data module
#

def registerSampleData():
    """
    Add data sets to Sample Data module.
    """
    import SampleData
    iconsPath = os.path.join(os.path.dirname(__file__), 'Resources/Icons')
    file_sha512 = "b648140f38d2c3189388a35fea65ef3b4311237de8c454c6b98480d84b139ec8afb8ec5881c5d9513cdc208ae781e1e442988be81564adff77edcfb30b921a28"
    SampleData.SampleDataLogic.registerCustomSampleDataSource(
        category='ITKANTs',
        sampleName='ITKANTsPhantomRF',
        thumbnailFileName=os.path.join(iconsPath, 'SampleRF.png'),
        uris=f"https://data.kitware.com:443/api/v1/file/hashsum/SHA512/{file_sha512}/download",  # "https://data.kitware.com/api/v1/item/57b5d5d88d777f10f269444b/download", "https://data.kitware.com/api/v1/file/57b5d5d88d777f10f269444f/download",
        fileNames='uniform_phantom_8.9_MHz.mha',
        checksums=f'SHA512:{file_sha512}',
        nodeNames='ITKANTsPhantomRF'
    )


@parameterNodeWrapper
class ANTsRegistrationParameterNode:
    """
    The parameters needed by module.

    fixedVolume - The fixed image.
    movingVolume - The moving image.
    samplingRate - Percentage of pixels to use to evaluate the metric.
    forwardTransform - The output transform (which can be used to resample moving image to fixed image grid).
    """

    fixedVolume: vtkMRMLScalarVolumeNode
    movingVolume: vtkMRMLScalarVolumeNode
    initialTransform: vtkMRMLTransformNode
    forwardTransform: vtkMRMLTransformNode
    transformType: str = "Affine"
    gradientStep: Annotated[float, WithinRange(0, 10.0)] = 0.2
    affineMetric: str = "Mattes"
    synMetric: str = "Mattes"
    samplingRate: Annotated[float, WithinRange(0, 1.0)] = 0.2


class ANTsRegistrationWidget(ScriptedLoadableModuleWidget, VTKObservationMixin):
    """Uses ScriptedLoadableModuleWidget base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self, parent=None) -> None:
        """Called when the user opens the module the first time and the widget is initialized."""
        ScriptedLoadableModuleWidget.__init__(self, parent)
        VTKObservationMixin.__init__(self)  # needed for parameter node observation
        self.logic = None
        self._parameterNode = None
        self._parameterNodeGuiTag = None

    def setup(self) -> None:
        """Called when the user opens the module the first time and the widget is initialized."""
        ScriptedLoadableModuleWidget.setup(self)

        # Load widget from .ui file (created by Qt Designer).
        # Additional widgets can be instantiated manually and added to self.layout.
        uiWidget = slicer.util.loadUI(self.resourcePath("UI/ANTsRegistration.ui"))
        self.layout.addWidget(uiWidget)
        self.ui = slicer.util.childWidgetVariables(uiWidget)

        # Set scene in MRML widgets. Make sure that in Qt designer the top-level qMRMLWidget's
        # "mrmlSceneChanged(vtkMRMLScene*)" signal in is connected to each MRML widget's.
        # "setMRMLScene(vtkMRMLScene*)" slot.
        uiWidget.setMRMLScene(slicer.mrmlScene)

        # Create logic class. Logic implements all computations that should be possible to run
        # in batch mode, without a graphical user interface.
        self.logic = ANTsRegistrationLogic()

        # Connections

        # These connections ensure that we update parameter node when scene is closed
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.StartCloseEvent, self.onSceneStartClose)
        self.addObserver(slicer.mrmlScene, slicer.mrmlScene.EndCloseEvent, self.onSceneEndClose)

        # Buttons
        self.ui.applyButton.connect("clicked(bool)", self.onApplyButton)

        # Make sure parameter node is initialized (needed for module reload)
        self.initializeParameterNode()

    def cleanup(self) -> None:
        """Called when the application closes and the module widget is destroyed."""
        self.removeObservers()

    def enter(self) -> None:
        """Called each time the user opens this module."""
        # Make sure parameter node exists and observed
        self.initializeParameterNode()

    def exit(self) -> None:
        """Called each time the user opens a different module."""
        # Do not react to parameter node changes (GUI will be updated when the user enters into the module)
        if self._parameterNode:
            self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
            self._parameterNodeGuiTag = None
            self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)

    def onSceneStartClose(self, caller, event) -> None:
        """Called just before the scene is closed."""
        # Parameter node will be reset, do not use it anymore
        self.setParameterNode(None)

    def onSceneEndClose(self, caller, event) -> None:
        """Called just after the scene is closed."""
        # If this module is shown while the scene is closed then recreate a new parameter node immediately
        if self.parent.isEntered:
            self.initializeParameterNode()

    def initializeParameterNode(self) -> None:
        """Ensure parameter node exists and observed."""
        # Parameter node stores all user choices in parameter values, node selections, etc.
        # so that when the scene is saved and reloaded, these settings are restored.

        self.setParameterNode(self.logic.getParameterNode())

        # Select default input nodes if nothing is selected yet to save a few clicks for the user
        if not self._parameterNode.fixedVolume:
            firstVolumeNode = slicer.mrmlScene.GetFirstNodeByClass("vtkMRMLScalarVolumeNode")
            if firstVolumeNode:
                self._parameterNode.fixedVolume = firstVolumeNode
                self._parameterNode.movingVolume = firstVolumeNode

    def setParameterNode(self, inputParameterNode: Optional[ANTsRegistrationParameterNode]) -> None:
        """
        Set and observe parameter node.
        Observation is needed because when the parameter node is changed then the GUI must be updated immediately.
        """

        if self._parameterNode:
            self._parameterNode.disconnectGui(self._parameterNodeGuiTag)
            self.removeObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)
        self._parameterNode = inputParameterNode
        if self._parameterNode:
            # Note: in the .ui file, a Qt dynamic property called "SlicerParameterName" is set on each
            # ui element that needs connection.
            self._parameterNodeGuiTag = self._parameterNode.connectGui(self.ui)
            self.addObserver(self._parameterNode, vtk.vtkCommand.ModifiedEvent, self._checkCanApply)
            self._checkCanApply()

    def _checkCanApply(self, caller=None, event=None) -> None:
        if self._parameterNode and self._parameterNode.fixedVolume and self._parameterNode.movingVolume and self._parameterNode.forwardTransform:
            self.ui.applyButton.toolTip = _("Compute output volume")
            self.ui.applyButton.enabled = True
        else:
            self.ui.applyButton.toolTip = _("Select input and output volume nodes")
            self.ui.applyButton.enabled = False

    def onApplyButton(self) -> None:
        """Run processing when user clicks "Apply" button."""
        with slicer.util.tryWithErrorDisplay(_("Failed to compute results."), waitCursor=True):
            # Compute output
            self.logic.process(self.ui.fixedSelector.currentNode(), self.ui.movingSelector.currentNode(),
                               self.ui.outputSelector.currentNode(), self.ui.affineMetricWidget.currentText,
                               self.ui.samplingRateSliderWidget.value)


#
# ANTsRegistrationLogic
#


class ANTsRegistrationLogic(ITKANTsCommonLogic):
    """This class should implement all the actual
    computation done by your module.  The interface
    should be such that other python code can import
    this class and make use of the functionality without
    requiring an instance of the Widget.
    Uses ScriptedLoadableModuleLogic base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def __init__(self) -> None:
        """Called when the logic class is instantiated. Can be used for initializing member variables."""
        ITKANTsCommonLogic.__init__(self)

    def getParameterNode(self):
        return ANTsRegistrationParameterNode(super().getParameterNode())

    def process(self,
                fixedVolume: vtkMRMLScalarVolumeNode,
                movingVolume: vtkMRMLScalarVolumeNode,
                forwardTransform: vtkMRMLTransformNode,
                metric: str = "Mattes",
                samplingRate: float = 0.2,
                ) -> None:
        """
        Run the processing algorithm.
        Can be used without GUI widget.
        :param fixedVolume: volume to be thresholded
        :param outputVolume: thresholding result
        :param samplingRate: values above/below this threshold will be set to 0
        :param showResult: show output volume in slice viewers
        """

        if not fixedVolume or not movingVolume or not forwardTransform:
            raise ValueError("Input volumes or output transform are invalid")

        import time

        logging.info('Instantiating the filter')
        itk = self.itk
        fixedImage = slicer.util.itkImageFromVolume(fixedVolume)
        ants_reg = itk.ANTSRegistration[type(fixedImage), type(fixedImage)].New()  # TODO: update name
        ants_reg.SetFixedImage(fixedImage)
        movingImage = slicer.util.itkImageFromVolume(movingVolume)
        ants_reg.SetMovingImage(fixedImage)
        ants_reg.SetSamplingRate(samplingRate)

        logging.info('Processing started')
        startTime = time.time()
        ants_reg.Update()
        outTransform = ants_reg.GetForwardTransform()
        print(outTransform)
        # TODO: set this to the output transform node
        # slicer.util.updateTransformMatrixFromArray
        # vtkITKTransformConverter.CreateVTKTransformFromITK()
        # slicer.util.updateTransformFromITKTransform(outTransform, forwardTransform)

        # slicer.util.updateVolumeFromITKImage(outputVolumeNode, itkImage)
        # slicer.util.setSliceViewerLayers(background=outputVolumeNode, fit=True, rotateToVolumePlane=True)
        stopTime = time.time()

        stopTime = time.time()
        logging.info(f"Processing completed in {stopTime-startTime:.2f} seconds")



class ANTsRegistrationTest(ScriptedLoadableModuleTest):
    """
    This is the test case for your scripted module.
    Uses ScriptedLoadableModuleTest base class, available at:
    https://github.com/Slicer/Slicer/blob/main/Base/Python/slicer/ScriptedLoadableModule.py
    """

    def setUp(self):
        """Do whatever is needed to reset the state - typically a scene clear will be enough."""
        slicer.mrmlScene.Clear()

    def runTest(self):
        """Run as few or as many tests as needed here."""
        self.setUp()
        self.test_ANTsRegistration1()

    def test_ANTsRegistration1(self):
        """Ideally you should have several levels of tests.  At the lowest level
        tests should exercise the functionality of the logic with different inputs
        (both valid and invalid).  At higher levels your tests should emulate the
        way the user would interact with your code and confirm that it still works
        the way you intended.
        One of the most important features of the tests is that it should alert other
        developers when their changes will have an impact on the behavior of your
        module.  For example, if a developer removes a feature that you depend on,
        your test should break so they know that the feature is needed.
        """

        self.delayDisplay("Starting the test")

        # Get/create input data

        import SampleData

        registerSampleData()
        fixedVolume = SampleData.downloadSample("ITKANTsPhantomRF")
        self.delayDisplay("Loaded test data set")

        inputScalarRange = fixedVolume.GetImageData().GetScalarRange()
        self.assertEqual(inputScalarRange[0], -4569)
        self.assertEqual(inputScalarRange[1], 4173)

        outputVolume = slicer.mrmlScene.AddNewNodeByClass("vtkMRMLScalarVolumeNode")

        # Test the module logic

        logic = ANTsRegistrationLogic()

        # Test algorithm with axis of propagation: 2
        logic.process(fixedVolume, outputVolume, 2)
        outputScalarRange = outputVolume.GetImageData().GetScalarRange()
        self.assertAlmostEqual(outputScalarRange[0], 0, places=5)
        self.assertAlmostEqual(outputScalarRange[1], 3.65992, places=5)

        # Test algorithm with axis of propagation: 1
        logic.process(fixedVolume, outputVolume, 1)
        outputScalarRange = outputVolume.GetImageData().GetScalarRange()
        self.assertAlmostEqual(outputScalarRange[0], 0.027904, places=5)
        self.assertAlmostEqual(outputScalarRange[1], 3.67797, places=5)

        # Test algorithm with axis of propagation: 0
        logic.process(fixedVolume, outputVolume, 0)
        outputScalarRange = outputVolume.GetImageData().GetScalarRange()
        self.assertAlmostEqual(outputScalarRange[0], 0.00406048, places=5)
        self.assertAlmostEqual(outputScalarRange[1], 3.66787, places=5)

        file_sha512 = "27998dfea16be10830384536f021f42f96c3f7095c9e5a1e983a10c37d4eddea514b45f217234eeccf062e9bdd0f811c49698658689e62924f6f96c0173f3176"
        import SampleData
        expectedResult = SampleData.downloadFromURL(
            nodeNames='ANTsRegistrationTestOutput',
            fileNames='GenerateANTsRegistrationTestOutput.mha',
            uris=f"https://data.kitware.com:443/api/v1/file/hashsum/SHA512/{file_sha512}/download",
            checksums=f'SHA512:{file_sha512}',
            loadFiles=True)

        itk = logic.itk
        FloatImage = itk.Image[itk.F, 3]
        comparer = itk.ComparisonImageFilter[FloatImage, FloatImage].New()
        comparer.SetValidInput(logic.getITKImageFromVolumeNode(expectedResult[0]))
        comparer.SetTestInput(logic.getITKImageFromVolumeNode(outputVolume))
        comparer.SetDifferenceThreshold(1e-5)
        comparer.Update()
        self.assertEqual(comparer.GetNumberOfPixelsWithDifferences(), 0)

        self.delayDisplay('Test passed')
